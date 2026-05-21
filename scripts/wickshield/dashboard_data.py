"""WickShield 看板数据聚合（供 Flask API）。"""

from __future__ import annotations



import json

import os

from typing import Any, Dict, List, Optional



_DASHBOARD_CACHE_REQUIRED_KEYS = frozenset(
    {
        "solvency_ratio",
        "global_cap_remaining",
        "cap_24h",
        "global_payout_today",
        "watch_symbols",
        "recent_claims",
        "state_date",
    }
)


def _dashboard_cache_is_complete(cached: Dict[str, Any]) -> bool:
    if not isinstance(cached, dict):
        return False
    # 单元测试 stub 或历史脏数据
    if "test" in cached and "solvency_ratio" not in cached:
        return False
    return _DASHBOARD_CACHE_REQUIRED_KEYS.issubset(cached.keys())


from .monitor import (

    CLAIMS_LOG,

    RESET_LOG,

    STATE_FILE,

    _load_state,

    _pool_coverage,

    _today_iso,

    _fetch_markets_parallel,

    _watch_symbols,

    live_snapshot_from_check,

    run_live_check,

)

from .payout_calc import calc_payout, get_threshold

from .premium_calc import calc_premium

from .rate_table import load_rate_table

from .claims_stats import monthly_payout_surcharge_detail
from .solvency_check import SolvencyRiskManager





def format_claim_for_dashboard(claim: Dict[str, Any]) -> Dict[str, Any]:
    """拒赔/未批准不展示拟赔金额（前端显示 —）。"""
    out = dict(claim)
    if str(out.get("decision", "")).lower() != "approved":
        out["final_payout"] = None
    return out


def _read_claims_tail(limit: int = 30) -> List[Dict[str, Any]]:
    try:
        from .claims_db import CLAIMS_DB_PATH, claims_db_enabled, recent_claims

        if claims_db_enabled() and CLAIMS_DB_PATH.is_file():
            rows = recent_claims(limit)
            if rows:
                return rows
    except Exception:
        pass

    if not CLAIMS_LOG.is_file():
        return []

    lines = CLAIMS_LOG.read_text(encoding="utf-8").strip().splitlines()
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(out))





def _monitor_amount() -> float:

    return float(os.environ.get("WICKSHIELD_MONITOR_AMOUNT", "1000"))





def _estimate_symbol_live(

    symbol: str,

    *,

    global_payout_today: float,

    solvency_ratio: float,

    rate_entry: Optional[Dict[str, Any]] = None,

) -> Dict[str, Any]:

    """

    不拉交易所时的估算行：动态封顶 + 全局剩余额度（来自精算引擎）。

    1h 振幅 / 理赔状态需 monitor 缓存或勾选 live 刷新。

    """

    amt = _monitor_amount()

    current_atr = None

    base_atr = None

    spike_count_1h = 0

    if rate_entry:

        avg_amp = rate_entry.get("avg_max_amplitude_percent")

        if avg_amp is not None:

            current_atr = float(avg_amp)

            base_atr = float(avg_amp)

        spike = rate_entry.get("hits_per_day")

        if spike is not None:

            spike_count_1h = min(int(float(spike)), 20)



    premium = calc_premium(

        amount=amt,

        symbol=symbol,

        days=int(os.environ.get("WICKSHIELD_MONITOR_POLICY_DAYS", "7")),

        leverage=int(os.environ.get("WICKSHIELD_MONITOR_LEVERAGE", "10")),

        solvency_ratio=solvency_ratio,

        current_atr=current_atr,

        base_atr=base_atr,

        spike_count_1h=spike_count_1h,

        use_report_risk=True,

    )

    pr = (premium.get("data") or {}) if premium.get("success") else {}

    dynamic_cap = pr.get("dynamic_cap_ratio")

    if dynamic_cap is None and pr.get("cap_limit_percent") is not None:

        dynamic_cap = float(pr["cap_limit_percent"]) / 100.0



    payout = calc_payout(

        coverage=amt,

        symbol=symbol,

        actual_amplitude=0.0,

        global_payout_today=global_payout_today,

        solvency_ratio=solvency_ratio,

    )

    pd = (payout.get("data") or {}) if payout.get("success") else {}

    threshold = float(get_threshold(symbol))



    return {

        "amplitude": None,

        "decision": "pending",

        "reason": "等待 monitor 拉取实时行情，或勾选「拉交易所 live」",

        "dynamic_cap_ratio": dynamic_cap,

        "global_cap_remaining": pd.get("global_cap_remaining"),

        "threshold_percent": threshold,

        "final_payout_if_triggered": 0.0,

        "triggered": False,

        "source": "estimated",

    }





def _merge_symbol_live(

    symbol: str,

    *,

    global_payout_today: float,

    solvency_ratio: float,

    pool: float,

    coverage: float,

    state: Dict[str, Any],

    rate_entry: Optional[Dict[str, Any]],

    live_refresh: bool,

    market: Optional[Dict[str, Any]] = None,

) -> Dict[str, Any]:

    if live_refresh:

        chk = run_live_check(

            symbol,

            global_payout_today=global_payout_today,

            pool=pool,

            coverage=coverage,

            solvency_ratio=solvency_ratio,

            market=market,

        )

        if chk.get("success"):

            snap = live_snapshot_from_check(chk)

            snap["source"] = "live"

            return snap



    snap = _estimate_symbol_live(

        symbol,

        global_payout_today=global_payout_today,

        solvency_ratio=solvency_ratio,

        rate_entry=rate_entry,

    )

    cached = (state.get("last_live") or {}).get(symbol)

    if isinstance(cached, dict):

        for key in (

            "checked_at",

            "exchange",
            "lead_exchange",
            "exchanges_used",
            "exchanges_count",

            "amplitude",

            "decision",

            "reason",

            "dynamic_cap_ratio",

            "global_cap_remaining",

            "threshold_percent",

            "final_payout_if_triggered",

            "triggered",

        ):

            if cached.get(key) is not None:

                snap[key] = cached[key]

        snap["source"] = "cached"

    return snap





def build_dashboard_snapshot(
    live_refresh: bool = False,
    *,
    skip_cache: bool = False,
) -> Dict[str, Any]:
    if not live_refresh and not skip_cache:
        from .monitor_cache import clear_monitor_snapshot, load_monitor_snapshot

        cached, hit = load_monitor_snapshot()
        if cached and hit:
            if _dashboard_cache_is_complete(cached):
                cached = dict(cached)
                cached["from_cache"] = True
                cached["cache_hit"] = True
                return cached
            clear_monitor_snapshot()

    pool, coverage = _pool_coverage()

    solvency = SolvencyRiskManager.check_risk(pool, coverage)

    sol_data = solvency.get("data") or {}

    ratio = float(sol_data.get("ratio") or 0)

    limits = sol_data.get("coverage_limits") or {}



    state = _load_state(persist_rollover=False)

    today = _today_iso()

    gpt = float(state.get("global_payout_today") or 0)

    cap_24h = float(limits.get("daily_24h_max") or 0)

    global_remaining = max(cap_24h - gpt, 0.0)



    symbols_out: List[Dict[str, Any]] = []

    rate_cache = load_rate_table() or {}

    rate_symbols = rate_cache.get("symbols") or {}

    watch = _watch_symbols()
    live_markets: Dict[str, Dict[str, Any]] = {}
    if live_refresh and watch:
        live_markets, _ = _fetch_markets_parallel(watch)



    for sym in watch:

        row: Dict[str, Any] = {"symbol": sym}

        cached = rate_symbols.get(sym) or {}

        if cached:

            row["base_daily_rate_percent"] = cached.get("base_daily_rate_percent")

            ins = cached.get("insurance_risk") or {}

            row["risk_grade"] = ins.get("risk_grade")

            row["risk_subgrade"] = cached.get("risk_subgrade") or ins.get("risk_subgrade")

            row["pricing_coefficient"] = cached.get("pricing_coefficient") or ins.get(
                "pricing_coefficient"
            )

            row["risk_score"] = ins.get("risk_score_0_100")



        row["live"] = _merge_symbol_live(

            sym,

            global_payout_today=gpt,

            solvency_ratio=ratio,

            pool=pool,

            coverage=coverage,

            state=state,

            rate_entry=cached or None,

            live_refresh=live_refresh,

            market=live_markets.get(sym),

        )

        symbols_out.append(row)



    last_reset = None

    if RESET_LOG.is_file():

        lines = RESET_LOG.read_text(encoding="utf-8").strip().splitlines()

        if lines:

            try:

                last_reset = json.loads(lines[-1])

            except json.JSONDecodeError:

                last_reset = None



    return {

        "success": True,

        "updated_at": today,

        "state_date": state.get("date"),

        "last_reset": last_reset,

        "pool_total": pool,

        "total_coverage": coverage,

        "solvency_ratio": ratio,

        "solvency_status": sol_data.get("status"),

        "solvency_actions": sol_data.get("actions"),

        "cap_single": float(limits.get("single_max") or 0),

        "cap_24h": cap_24h,

        "global_payout_today": gpt,

        "global_cap_remaining": global_remaining,

        "global_cap_used_percent": round(gpt / cap_24h * 100, 2) if cap_24h > 0 else 0,

        "rate_table_generated_at": rate_cache.get("generated_at"),

        "rate_table_symbol_count": rate_cache.get("symbol_count"),

        "watch_symbols": symbols_out,

        "recent_claims": [
            format_claim_for_dashboard(c) for c in _read_claims_tail(20)
        ],

        "monthly_surcharge": monthly_payout_surcharge_detail(),

        "monitor_state_path": "data/wickshield/monitor_state.json" if STATE_FILE.is_file() else None,

        "live_refresh": live_refresh,

    }


