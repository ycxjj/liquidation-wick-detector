"""
日报数据 → 币种风险画像 → 动态保费乘数

读取 data/reports/YYYY-MM-DD/{exchange}.json，聚合历史插针频率与振幅特征，
供 premium_calc 做数据驱动的基础费率调整（对齐设计文档 §2.2 因子思路）。
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .constants import (
    BASE_RATES_DAILY_PCT,
    RISK_SUBGRADE_COEFF_A_MAX,
    RISK_SUBGRADE_COEFF_A_MINUS_MAX,
    RISK_SUBGRADE_COEFF_A_PLUS_MAX,
    RISK_SUBGRADE_PREMIUM_MULT,
)

_ROOT = Path(__file__).resolve().parents[2]
REPORTS_DIR = _ROOT / "data" / "reports"

DEFAULT_EXCHANGES = ("gate", "binanceusdm", "okx", "bybit", "bitget", "mexc")
DEFAULT_LOOKBACK_DAYS = 30

# 平静期参考：日均插针次数、典型最大振幅 (%)
BASELINE_HITS_PER_DAY = Decimal("0.05")
BASELINE_MAX_AMP_PCT = Decimal("2.0")

REPORT_MULT_MIN = Decimal("0.75")
REPORT_MULT_MAX = Decimal("2.0")

# 无静态表时的冷启动底价（%%/日），仅当完全没有日报样本时使用
COLD_START_FLOOR_PCT: Dict[str, Decimal] = {
    "BTC/USDT": Decimal("0.05"),
    "ETH/USDT": Decimal("0.08"),
    "SOL/USDT": Decimal("0.15"),
    "LAB/USDT": Decimal("0.8"),
    "DEFAULT": Decimal("0.30"),
}
MAX_BASE_FROM_REPORT_PCT = Decimal("3.0")


def _list_report_dates(lookback_days: int, end_date: Optional[date] = None) -> List[date]:
    end = end_date or date.today()
    dates: List[date] = []
    for i in range(lookback_days):
        d = end - timedelta(days=i + 1)
        if (REPORTS_DIR / d.isoformat()).is_dir():
            dates.append(d)
    return sorted(dates)


def _load_day_reports(
    report_date: date,
    exchanges: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    day_dir = REPORTS_DIR / report_date.isoformat()
    if not day_dir.is_dir():
        return []
    ex_list = exchanges or list(DEFAULT_EXCHANGES)
    payloads: List[Dict[str, Any]] = []
    for ex in ex_list:
        path = day_dir / f"{ex}.json"
        if not path.is_file():
            continue
        try:
            with open(path, encoding="utf-8") as f:
                payloads.append(json.load(f))
        except (json.JSONDecodeError, OSError):
            continue
    return payloads


def _static_base_pct(symbol: str) -> Decimal:
    sym = _normalize_symbol(symbol)
    if sym in BASE_RATES_DAILY_PCT:
        return BASE_RATES_DAILY_PCT[sym]
    base = sym.split("/")[0]
    for key, val in BASE_RATES_DAILY_PCT.items():
        if key != "DEFAULT" and key.split("/")[0] == base:
            return val
    return BASE_RATES_DAILY_PCT["DEFAULT"]


def _normalize_symbol(symbol: str) -> str:
    s = symbol.strip().upper()
    if "/" not in s and s.endswith("USDT"):
        return f"{s[:-4]}/USDT"
    return s


def _find_row(rows: List[dict], symbol: str) -> Optional[dict]:
    target = _normalize_symbol(symbol)
    for row in rows:
        if _normalize_symbol(str(row.get("symbol", ""))) == target:
            return row
    return None


def aggregate_symbol_from_reports(
    symbol: str,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    exchanges: Optional[List[str]] = None,
    end_date: Optional[date] = None,
) -> Dict[str, Any]:
    """
    跨交易所、跨日期聚合单币种日报指标。
    每个日历日取各所该币 hit_count / max_amplitude 的最大值（保守风控）。
    """
    symbol = _normalize_symbol(symbol)
    dates = _list_report_dates(lookback_days, end_date)
    if not dates:
        return {
            "success": False,
            "error": f"未找到日报目录（{REPORTS_DIR}，近 {lookback_days} 天）",
            "symbol": symbol,
        }

    daily: List[Dict[str, Any]] = []
    total_hits = 0
    hit_days = 0
    amp_samples: List[float] = []
    spike_events: List[Dict[str, Any]] = []
    spike_amps_when_hit: List[float] = []

    for d in dates:
        day_hits = 0
        day_max_amp = 0.0
        sources: Set[str] = set()

        for payload in _load_day_reports(d, exchanges):
            ex = str(payload.get("exchange", ""))
            row = _find_row(payload.get("rows") or [], symbol)
            if not row or row.get("error"):
                continue
            klines = int(row.get("total_klines") or 0)
            if klines <= 0:
                continue
            sources.add(ex)
            hc = int(row.get("hit_count") or 0)
            ma = float(row.get("max_amplitude") or 0)
            day_hits = max(day_hits, hc)
            day_max_amp = max(day_max_amp, ma)
            for hit in row.get("hits") or []:
                if not isinstance(hit, dict):
                    continue
                amp = float(hit.get("amplitude") or 0)
                spike_events.append(
                    {
                        "date": d.isoformat(),
                        "exchange": ex,
                        "timestamp": hit.get("timestamp"),
                        "direction": hit.get("direction"),
                        "amplitude_percent": amp,
                    }
                )
                if amp > 0:
                    spike_amps_when_hit.append(amp)

        if not sources:
            continue

        total_hits += day_hits
        if day_hits > 0:
            hit_days += 1
        if day_max_amp > 0:
            amp_samples.append(day_max_amp)

        daily.append(
            {
                "date": d.isoformat(),
                "hit_count": day_hits,
                "max_amplitude": round(day_max_amp, 4),
                "exchanges_with_data": sorted(sources),
            }
        )

    sample_days = len(daily)
    if sample_days == 0:
        return {
            "success": False,
            "error": f"近 {lookback_days} 天日报中无 {symbol} 有效数据",
            "symbol": symbol,
            "dates_scanned": [d.isoformat() for d in dates],
        }

    hits_per_day = Decimal(str(total_hits)) / Decimal(sample_days)
    avg_max_amp = (
        Decimal(str(sum(amp_samples) / len(amp_samples))) if amp_samples else Decimal("0")
    )
    peak_max_amp = Decimal(str(max(amp_samples))) if amp_samples else Decimal("0")
    avg_spike_amp = (
        Decimal(str(sum(spike_amps_when_hit) / len(spike_amps_when_hit)))
        if spike_amps_when_hit
        else Decimal("0")
    )

    return {
        "success": True,
        "symbol": symbol,
        "lookback_days": lookback_days,
        "sample_days": sample_days,
        "date_range": {
            "from": daily[0]["date"],
            "to": daily[-1]["date"],
        },
        "total_hits": total_hits,
        "hit_days": hit_days,
        "hits_per_day": float(hits_per_day.quantize(Decimal("0.0001"))),
        "avg_max_amplitude_percent": float(avg_max_amp.quantize(Decimal("0.0001"))),
        "peak_max_amplitude_percent": float(peak_max_amp.quantize(Decimal("0.0001"))),
        "spike_events_count": len(spike_events),
        "avg_confirmed_spike_amplitude_percent": float(avg_spike_amp.quantize(Decimal("0.0001"))),
        "spike_events": spike_events[-20:],
        "daily_series": daily,
    }


def _cold_start_floor_pct(symbol: str) -> Decimal:
    sym = _normalize_symbol(symbol)
    floor = COLD_START_FLOOR_PCT.get(sym, COLD_START_FLOOR_PCT["DEFAULT"])
    base_coin = sym.split("/")[0]
    for k, v in COLD_START_FLOOR_PCT.items():
        if k != "DEFAULT" and k.split("/")[0] == base_coin:
            return v
    return floor


def compute_pricing_coefficient(
    *,
    effective_base_pct: Decimal,
    floor_pct: Decimal,
    peak_amp_pct: float = 0.0,
    hits_per_day: float = 0.0,
) -> Decimal:
    """
    定价系数：有效日费率相对冷启动底价的抬升，并叠加峰值振幅与插针频率。
    用于 A 档内 A-/A/A+ 分档（约 1~5 / 5~10 / 10~25）。
    """
    ratio = effective_base_pct / max(floor_pct, Decimal("0.01"))
    peak = Decimal(str(peak_amp_pct))
    hits = Decimal(str(hits_per_day))
    vol = Decimal("1") + peak / Decimal("4")
    freq = Decimal("1") + hits * Decimal("6")
    raw = ratio * vol * freq
    return max(Decimal("1"), raw).quantize(Decimal("0.01"))


def classify_risk_subgrade(pricing_coefficient: Decimal, parent_grade: str) -> str:
    """A 档按定价系数拆分为 A-/A/A+；B/C/D 保持原字母档。"""
    g = (parent_grade or "A").upper()
    if g != "A":
        return g
    c = pricing_coefficient
    if c < RISK_SUBGRADE_COEFF_A_MINUS_MAX:
        return "A-"
    if c < RISK_SUBGRADE_COEFF_A_MAX:
        return "A"
    if c < RISK_SUBGRADE_COEFF_A_PLUS_MAX:
        return "A+"
    return "A+"


def risk_subgrade_premium_multiplier(subgrade: str) -> Decimal:
    return RISK_SUBGRADE_PREMIUM_MULT.get(subgrade, Decimal("1"))


def build_insurance_risk_summary(
    profile: Dict[str, Any],
    *,
    symbol: str = "",
    effective_base_pct: Optional[Decimal] = None,
) -> Dict[str, Any]:
    """
    将日报「插针记录」翻译为保险精算可读的定价输入。
    """
    if not profile.get("success"):
        return {}

    hits_pd = float(profile.get("hits_per_day", 0))
    avg_amp = float(profile.get("avg_max_amplitude_percent", 0))
    peak_amp = float(profile.get("peak_max_amplitude_percent", 0))
    hit_days = int(profile.get("hit_days", 0))
    sample_days = int(profile.get("sample_days", 1))
    confirmed = int(profile.get("spike_events_count", 0))

    risk_score = min(
        100.0,
        hits_pd * 25.0 + avg_amp * 2.5 + peak_amp * 0.4 + confirmed * 0.5,
    )
    if risk_score < 25:
        grade, freq_label = "A", "低"
    elif risk_score < 50:
        grade, freq_label = "B", "中低"
    elif risk_score < 75:
        grade, freq_label = "C", "中高"
    else:
        grade, freq_label = "D", "高"

    eff = effective_base_pct
    if eff is None and symbol:
        eff = derive_base_daily_rate_from_reports(profile, symbol)
    floor = _cold_start_floor_pct(symbol) if symbol else COLD_START_FLOOR_PCT["DEFAULT"]
    coeff = compute_pricing_coefficient(
        effective_base_pct=eff or floor,
        floor_pct=floor,
        peak_amp_pct=peak_amp,
        hits_per_day=hits_pd,
    )
    subgrade = classify_risk_subgrade(coeff, grade)
    sub_mult = float(risk_subgrade_premium_multiplier(subgrade))

    return {
        "risk_grade": grade,
        "risk_subgrade": subgrade,
        "pricing_coefficient": float(coeff),
        "risk_subgrade_premium_multiplier": sub_mult,
        "cold_start_floor_percent": float(floor),
        "risk_score_0_100": round(risk_score, 1),
        "claim_frequency_label": freq_label,
        "hit_days_ratio": round(hit_days / max(sample_days, 1), 3),
        "confirmed_spike_events": confirmed,
        "pricing_rationale": (
            f"近{sample_days}日跨所聚合：日均插针信号{hits_pd:.2f}次，"
            f"日均最大振幅{avg_amp:.2f}%，峰值{peak_amp:.2f}%；"
            f"精算评级{grade}→{subgrade}（系数{coeff}，理赔倾向{freq_label}）"
        ),
        "pricing_factors": {
            "spike_frequency_per_day": hits_pd,
            "volatility_avg_percent": avg_amp,
            "volatility_peak_percent": peak_amp,
            "confirmed_wick_records": confirmed,
            "effective_base_daily_rate_percent": float(eff) if eff is not None else None,
            "cold_start_floor_percent": float(floor),
        },
    }


def derive_base_daily_rate_from_reports(profile: Dict[str, Any], symbol: str) -> Decimal:
    """
    由日报直接推导基础日费率（%%），不依赖写死的 BASE_RATES 字典。
    """
    sym = _normalize_symbol(symbol)
    floor = COLD_START_FLOOR_PCT.get(sym, COLD_START_FLOOR_PCT["DEFAULT"])
    base_coin = sym.split("/")[0]
    for k, v in COLD_START_FLOOR_PCT.items():
        if k != "DEFAULT" and k.split("/")[0] == base_coin:
            floor = v
            break

    hits_pd = Decimal(str(profile.get("hits_per_day", 0)))
    avg_amp = Decimal(str(profile.get("avg_max_amplitude_percent", 0)))
    peak_amp = Decimal(str(profile.get("peak_max_amplitude_percent", 0)))

    derived = (
        floor
        + hits_pd * Decimal("0.15")
        + avg_amp * Decimal("0.04")
        + peak_amp * Decimal("0.01")
    )
    return max(floor, min(derived, MAX_BASE_FROM_REPORT_PCT)).quantize(Decimal("0.0001"))


def compute_report_premium_multiplier(profile: Dict[str, Any]) -> Decimal:
    """
    由日报画像生成保费乘数（乘在静态基础日费率上）。

    - 插针频率：相对平静期基准抬升
    - 振幅水平：avg / peak 超基准则加价
    """
    if not profile.get("success"):
        return Decimal("1.0")

    hits_per_day = Decimal(str(profile.get("hits_per_day", 0)))
    avg_amp = Decimal(str(profile.get("avg_max_amplitude_percent", 0)))
    peak_amp = Decimal(str(profile.get("peak_max_amplitude_percent", 0)))

    hit_adj = (hits_per_day - BASELINE_HITS_PER_DAY) / max(BASELINE_HITS_PER_DAY, Decimal("0.01"))
    hit_adj = max(Decimal("0"), min(hit_adj, Decimal("3")))
    freq_mult = Decimal("1") + hit_adj * Decimal("0.12")

    amp_ref = max(avg_amp, peak_amp * Decimal("0.5"))
    amp_adj = (amp_ref - BASELINE_MAX_AMP_PCT) / BASELINE_MAX_AMP_PCT
    amp_adj = max(Decimal("0"), min(amp_adj, Decimal("4")))
    amp_mult = Decimal("1") + amp_adj * Decimal("0.08")

    combined = freq_mult * amp_mult
    return max(REPORT_MULT_MIN, min(combined, REPORT_MULT_MAX)).quantize(Decimal("0.0001"))


def build_report_risk_profile(
    symbol: str,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    exchanges: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """完整风险画像 + 建议费率乘数 + 数据驱动后的基础日费率（%）。"""
    agg = aggregate_symbol_from_reports(symbol, lookback_days, exchanges)
    if not agg.get("success"):
        return agg

    floor = _static_base_pct(symbol)
    mult = compute_report_premium_multiplier(agg)
    effective_base = derive_base_daily_rate_from_reports(agg, symbol)
    insurance = build_insurance_risk_summary(
        agg, symbol=symbol, effective_base_pct=effective_base
    )

    return {
        **agg,
        "insurance_risk": insurance,
        "cold_start_floor_percent": float(floor),
        "report_premium_multiplier": float(mult),
        "effective_base_daily_rate_percent": float(effective_base),
        "pricing_mode": "daily_report_primary",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def resolve_base_daily_rate(
    symbol: str,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    exchanges: Optional[List[str]] = None,
    prefer_cache: bool = True,
) -> tuple[Decimal, str, Optional[Dict[str, Any]]]:
    """
    保费引擎统一入口：费率表缓存 → 实时日报聚合 → 冷启动底价。
    返回 (base_daily_pct, source, detail)
    source: rate_cache | daily_reports | cold_start
    """
    if prefer_cache:
        from .rate_table import get_cached_base_daily_pct

        cached, meta = get_cached_base_daily_pct(symbol)
        if cached is not None:
            return cached, "rate_cache", meta

    profile = build_report_risk_profile(symbol, lookback_days, exchanges)
    if profile.get("success"):
        return (
            Decimal(str(profile["effective_base_daily_rate_percent"])),
            "daily_reports",
            profile,
        )

    sym = _normalize_symbol(symbol)
    floor = COLD_START_FLOOR_PCT.get(sym, COLD_START_FLOOR_PCT["DEFAULT"])
    return floor, "cold_start", {"reason": profile.get("error", "无日报样本")}


def get_report_adjusted_base_pct(
    symbol: str,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    exchanges: Optional[List[str]] = None,
) -> tuple[Decimal, Optional[Dict[str, Any]]]:
    """兼容旧接口。"""
    base, _src, detail = resolve_base_daily_rate(symbol, lookback_days, exchanges)
    return base, detail if isinstance(detail, dict) and detail.get("success") else detail
