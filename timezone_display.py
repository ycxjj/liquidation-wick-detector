"""
根据请求 IP / CDN 头解析展示时区；日报存档不改，仅用于前端/API 展示换算。
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

# 常见国家 → IANA 时区（单时区国家；多时区国家取人口/金融中心优先）
COUNTRY_DEFAULT_TZ: Dict[str, str] = {
    "CN": "Asia/Shanghai",
    "HK": "Asia/Hong_Kong",
    "MO": "Asia/Macau",
    "TW": "Asia/Taipei",
    "JP": "Asia/Tokyo",
    "KR": "Asia/Seoul",
    "SG": "Asia/Singapore",
    "MY": "Asia/Kuala_Lumpur",
    "TH": "Asia/Bangkok",
    "VN": "Asia/Ho_Chi_Minh",
    "ID": "Asia/Jakarta",
    "PH": "Asia/Manila",
    "IN": "Asia/Kolkata",
    "AE": "Asia/Dubai",
    "SA": "Asia/Riyadh",
    "IL": "Asia/Jerusalem",
    "TR": "Europe/Istanbul",
    "RU": "Europe/Moscow",
    "GB": "Europe/London",
    "DE": "Europe/Berlin",
    "FR": "Europe/Paris",
    "IT": "Europe/Rome",
    "ES": "Europe/Madrid",
    "NL": "Europe/Amsterdam",
    "CH": "Europe/Zurich",
    "SE": "Europe/Stockholm",
    "PL": "Europe/Warsaw",
    "US": "America/New_York",
    "CA": "America/Toronto",
    "MX": "America/Mexico_City",
    "BR": "America/Sao_Paulo",
    "AR": "America/Argentina/Buenos_Aires",
    "AU": "Australia/Sydney",
    "NZ": "Pacific/Auckland",
}

TZ_LABELS: Dict[str, str] = {
    "Asia/Shanghai": "北京时间",
    "Asia/Hong_Kong": "香港时间",
    "Asia/Taipei": "台北时间",
    "Asia/Tokyo": "东京时间",
    "Asia/Seoul": "首尔时间",
    "Asia/Singapore": "新加坡时间",
    "Europe/London": "伦敦时间",
    "Europe/Berlin": "中欧时间",
    "Europe/Paris": "巴黎时间",
    "America/New_York": "美东时间",
    "America/Los_Angeles": "美西时间",
    "America/Chicago": "美中时间",
    "Australia/Sydney": "悉尼时间",
}

DEFAULT_TZ = "Asia/Shanghai"


def _header_get(headers: Any, name: str) -> str:
    if headers is None:
        return ""
    if hasattr(headers, "get"):
        return (headers.get(name) or headers.get(name.lower()) or "").strip()
    return ""


def _valid_iana(tz: str) -> bool:
    if not tz or "/" not in tz:
        return False
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(tz)
        return True
    except Exception:
        return False


def timezone_label(tz: str) -> str:
    return TZ_LABELS.get(tz, tz.replace("_", " "))


def resolve_display_timezone(
    headers: Any = None,
    *,
    query_tz: Optional[str] = None,
    client_ip: Optional[str] = None,
) -> Dict[str, Any]:
    """
    解析展示时区，优先级：
    1) 查询参数 tz（测试/手动）
    2) Cloudflare CF-Timezone
    3) CF-IPCountry 映射
    4) 其它 CDN 国家头
    5) 默认 Asia/Shanghai
    """
    if query_tz and _valid_iana(query_tz):
        return {
            "timezone": query_tz,
            "label": timezone_label(query_tz),
            "source": "query",
            "country": None,
        }

    cf_tz = _header_get(headers, "CF-Timezone")
    if _valid_iana(cf_tz):
        country = _header_get(headers, "CF-IPCountry") or None
        return {
            "timezone": cf_tz,
            "label": timezone_label(cf_tz),
            "source": "cloudflare_timezone",
            "country": country or None,
        }

    country = (
        _header_get(headers, "CF-IPCountry")
        or _header_get(headers, "X-Country-Code")
        or _header_get(headers, "CloudFront-Viewer-Country")
        or ""
    ).upper()[:2]

    if country and country in COUNTRY_DEFAULT_TZ:
        tz = COUNTRY_DEFAULT_TZ[country]
        return {
            "timezone": tz,
            "label": timezone_label(tz),
            "source": "ip_country",
            "country": country,
        }

    if country:
        return {
            "timezone": DEFAULT_TZ,
            "label": timezone_label(DEFAULT_TZ),
            "source": "ip_country_fallback",
            "country": country,
        }

    _ = client_ip  # 预留：可接 GeoIP2；当前无依赖时用默认
    return {
        "timezone": DEFAULT_TZ,
        "label": timezone_label(DEFAULT_TZ),
        "source": "default",
        "country": None,
    }


def resolve_from_flask_request(req: Any) -> Dict[str, Any]:
    query_tz = None
    if req and getattr(req, "args", None):
        query_tz = (req.args.get("tz") or "").strip() or None
    ip = None
    if req:
        ip = _header_get(req.headers, "X-Forwarded-For").split(",")[0].strip() or (
            getattr(req, "remote_addr", None)
        )
    return resolve_display_timezone(req.headers if req else None, query_tz=query_tz, client_ip=ip)
