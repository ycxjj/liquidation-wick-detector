"""
日报跑完后生成的动态费率表（跨交易所聚合）。
保费引擎优先读取本文件，无条目时再实时聚合日报或回退冷启动底价。
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from .report_risk import (
    DEFAULT_EXCHANGES,
    DEFAULT_LOOKBACK_DAYS,
    REPORTS_DIR,
    _list_report_dates,
    _load_day_reports,
    _normalize_symbol,
    build_report_risk_profile,
)

_ROOT = Path(__file__).resolve().parents[2]
RATES_DIR = _ROOT / "data" / "rates"
RATES_CACHE_FILE = RATES_DIR / "dynamic_rates.json"


def _collect_symbols_from_reports(lookback_days: int) -> List[str]:
    symbols: set[str] = set()
    for d in _list_report_dates(lookback_days):
        for payload in _load_day_reports(d, list(DEFAULT_EXCHANGES)):
            for row in payload.get("rows") or []:
                if int(row.get("total_klines") or 0) <= 0:
                    continue
                if row.get("error"):
                    continue
                symbols.add(_normalize_symbol(str(row.get("symbol", ""))))
    return sorted(s for s in symbols if s)


def rebuild_rate_table(
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    exchanges: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    扫描近 N 天全部交易所日报，为每个有效币种生成风险画像并写入 dynamic_rates.json。
    应在「当日所有交易所日报跑完」后调用一次。
    """
    dates = _list_report_dates(lookback_days)
    symbols = _collect_symbols_from_reports(lookback_days)
    if not symbols:
        return {
            "success": False,
            "error": f"无可用日报数据，无法生成费率表（{REPORTS_DIR}）",
        }

    ex_list = exchanges or list(DEFAULT_EXCHANGES)
    table: Dict[str, Any] = {}
    total = len(symbols)
    for i, sym in enumerate(symbols, start=1):
        if i == 1 or i % 50 == 0 or i == total:
            print(f"[wickshield] 费率画像 {i}/{total} {sym}", flush=True)
        profile = build_report_risk_profile(sym, lookback_days, ex_list)
        if profile.get("success"):
            ins = profile.get("insurance_risk") or {}
            table[sym] = {
                "base_daily_rate_percent": profile["effective_base_daily_rate_percent"],
                "report_premium_multiplier": profile.get("report_premium_multiplier"),
                "pricing_coefficient": ins.get("pricing_coefficient"),
                "risk_subgrade": ins.get("risk_subgrade"),
                "insurance_risk": ins,
                "pricing_mode": profile.get("pricing_mode"),
                "hits_per_day": profile.get("hits_per_day"),
                "avg_max_amplitude_percent": profile.get("avg_max_amplitude_percent"),
                "peak_max_amplitude_percent": profile.get("peak_max_amplitude_percent"),
                "sample_days": profile.get("sample_days"),
            }

    RATES_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "success": True,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "lookback_days": lookback_days,
        "exchanges": ex_list,
        "report_dates": [d.isoformat() for d in dates],
        "symbol_count": len(table),
        "symbols": table,
        "pricing_source": "daily_reports",
    }
    with open(RATES_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return payload


def load_rate_table() -> Optional[Dict[str, Any]]:
    if not RATES_CACHE_FILE.is_file():
        return None
    try:
        with open(RATES_CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def get_cached_base_daily_pct(symbol: str) -> tuple[Optional[Decimal], Optional[Dict[str, Any]]]:
    """从费率表读取基础日费率（%%）。"""
    cache = load_rate_table()
    if not cache:
        return None, None
    sym = _normalize_symbol(symbol)
    entry = (cache.get("symbols") or {}).get(sym)
    if not entry:
        base = sym.split("/")[0]
        for k, v in (cache.get("symbols") or {}).items():
            if k.split("/")[0] == base:
                entry = v
                break
    if not entry:
        return None, cache
    return Decimal(str(entry["base_daily_rate_percent"])), {
        "from_cache": True,
        "cache_generated_at": cache.get("generated_at"),
        "symbol_entry": entry,
    }


def refresh_rates_after_daily_reports(
    lookback_days: Optional[int] = None,
) -> Dict[str, Any]:
    """供 run_daily_report / daily_scan 流水线在日报完成后调用。"""
    if os.environ.get("DISABLE_RATE_TABLE_REFRESH", "").strip() in ("1", "true", "yes"):
        return {"success": False, "skipped": True, "reason": "DISABLE_RATE_TABLE_REFRESH"}
    days = lookback_days or int(os.environ.get("WICKSHIELD_RATE_LOOKBACK_DAYS", str(DEFAULT_LOOKBACK_DAYS)))
    return rebuild_rate_table(lookback_days=days)
