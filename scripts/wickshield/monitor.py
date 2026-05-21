"""
WickShield 实时理赔监控：周期性 live 检测 → 额度校验 → 记录/拒绝索赔。
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .payout_calc import calc_payout, get_threshold
from .premium_calc import calc_premium
from .solvency_check import SolvencyRiskManager

_ROOT = Path(__file__).resolve().parents[2]
_DATA = _ROOT / "data" / "wickshield"
STATE_FILE = _DATA / "monitor_state.json"
CLAIMS_LOG = _DATA / "claims_log.jsonl"
RESET_LOG = _DATA / "reset_log.jsonl"


def _tz_today() -> date:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(os.environ.get("DAILY_REPORT_TZ", "Asia/Shanghai"))).date()
    except Exception:
        return date.today()


def _today_iso() -> str:
    return _tz_today().isoformat()


def _fresh_state() -> Dict[str, Any]:
    return {"date": _today_iso(), "global_payout_today": 0.0, "claims": []}


def _read_raw_state() -> Optional[Dict[str, Any]]:
    if not STATE_FILE.is_file():
        return None
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _append_reset_log(entry: Dict[str, Any]) -> None:
    _DATA.mkdir(parents=True, exist_ok=True)
    with open(RESET_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def reset_daily_quota(*, source: str = "cron") -> Dict[str, Any]:
    """
    显式日切重置：清空 global_payout_today，恢复满额（由 cap_24h 推算）。
    建议 crontab 每天 00:00（Asia/Shanghai）执行。
    """
    _DATA.mkdir(parents=True, exist_ok=True)
    today = _today_iso()
    previous = _read_raw_state() or {}
    was_same_day = previous.get("date") == today

    st = _fresh_state()
    st["reset_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    st["reset_source"] = source
    if previous:
        st["previous_date"] = previous.get("date")
        st["previous_global_payout_today"] = float(previous.get("global_payout_today") or 0)
        last_checks = previous.get("last_checks")
        if isinstance(last_checks, dict):
            st["last_checks"] = last_checks

    _save_state(st)
    log_entry = {
        "ts": st["reset_at"],
        "source": source,
        "date": today,
        "previous_date": previous.get("date"),
        "previous_global_payout_today": float(previous.get("global_payout_today") or 0),
        "idempotent_same_day": was_same_day,
    }
    _append_reset_log(log_entry)

    pool, cov = _pool_coverage()
    solvency = SolvencyRiskManager.check_risk(pool, cov)
    limits = (solvency.get("data") or {}).get("coverage_limits") or {}
    cap_24h = float(limits.get("daily_24h_max") or 0)

    return {
        "success": True,
        "action": "reset_daily_quota",
        "date": today,
        "global_payout_today": 0.0,
        "global_cap_remaining": cap_24h,
        "cap_24h": cap_24h,
        "previous_date": previous.get("date"),
        "previous_global_payout_today": float(previous.get("global_payout_today") or 0),
        "idempotent_same_day": was_same_day,
        "reset_at": st["reset_at"],
    }


def _load_state(*, persist_rollover: bool = True) -> Dict[str, Any]:
    """读取今日状态；跨日时归零。persist_rollover=True 时落盘（monitor 用）。"""
    _DATA.mkdir(parents=True, exist_ok=True)
    today = _today_iso()
    raw = _read_raw_state()
    if raw and raw.get("date") == today:
        return raw

    prev_payout = float(raw.get("global_payout_today") or 0) if raw else 0.0
    st = _fresh_state()
    if raw:
        st["rollover_from"] = {
            "date": raw.get("date"),
            "global_payout_today": prev_payout,
        }
        last_checks = raw.get("last_checks")
        if isinstance(last_checks, dict):
            st["last_checks"] = last_checks

    if persist_rollover and raw and raw.get("date") != today:
        _save_state(st)
    return st


def _save_state(st: Dict[str, Any]) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=2, ensure_ascii=False)


def _append_claim_log(entry: Dict[str, Any]) -> None:
    with open(CLAIMS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _watch_symbols() -> List[str]:
    """
    监控币单优先级：
    1) 环境变量 WICKSHIELD_MONITOR_SYMBOLS（非空则只用此项）
    2) data/wickshield/monitor_symbols.json（cli symbols refresh 生成）
    3) 默认 4 币
    """
    env = os.environ.get("WICKSHIELD_MONITOR_SYMBOLS", "").strip()
    if env:
        return [s.strip() for s in env.split(",") if s.strip()]
    from .hot_symbols import load_monitor_symbols_file

    cached = load_monitor_symbols_file()
    if cached:
        return cached
    return ["LAB/USDT", "BTC/USDT", "ETH/USDT", "SOL/USDT"]


def _pool_coverage() -> tuple[float, float]:
    pool = float(os.environ.get("WICKSHIELD_POOL", "50000"))
    cov = float(os.environ.get("WICKSHIELD_COVERAGE", "100000"))
    return pool, cov


def _notify_chain(entry: Dict[str, Any]) -> None:
    """链上理赔占位：配置 WICKSHIELD_CHAIN_WEBHOOK 后 POST JSON。"""
    url = os.environ.get("WICKSHIELD_CHAIN_WEBHOOK", "").strip()
    if not url or entry.get("decision") != "approved":
        return
    try:
        body = json.dumps(entry, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            entry["chain_dispatch_status"] = resp.status
    except Exception as e:
        entry["chain_dispatch_error"] = str(e)


def live_snapshot_from_check(chk: Dict[str, Any]) -> Dict[str, Any]:
    """从 run_live_check 结果提取看板/缓存字段。"""
    pd = chk.get("payout") or {}
    return {
        "checked_at": chk.get("checked_at"),
        "exchange": (chk.get("market") or {}).get("exchange"),
        "lead_exchange": (chk.get("market") or {}).get("lead_exchange"),
        "exchanges_used": (chk.get("market") or {}).get("exchanges_used"),
        "exchanges_count": (chk.get("market") or {}).get("exchanges_count"),
        "amplitude": (chk.get("market") or {}).get("max_amplitude_1h_percent"),
        "decision": chk.get("decision"),
        "reason": chk.get("reason"),
        "dynamic_cap_ratio": chk.get("dynamic_cap_ratio"),
        "global_cap_remaining": pd.get("global_cap_remaining"),
        "threshold_percent": chk.get("threshold_percent"),
        "final_payout_if_triggered": pd.get("final_payout"),
        "triggered": chk.get("triggered"),
    }


def _monitor_market_kwargs() -> Dict[str, Any]:
    from .market_data import monitor_days_back

    return {
        "timeframe": os.environ.get("WICKSHIELD_MONITOR_TIMEFRAME", "5m"),
        "days_back": monitor_days_back(),
    }


def fetch_monitor_market(
    symbol: str,
    *,
    exchange: Optional[str] = None,
) -> Dict[str, Any]:
    """仅拉行情（可并行）；失败返回 success=False。"""
    from .market_data import build_market_snapshot

    try:
        market = build_market_snapshot(
            symbol=symbol,
            exchange=exchange or os.environ.get("WICKSHIELD_MONITOR_EXCHANGE"),
            **_monitor_market_kwargs(),
        )
        return {"success": True, "symbol": symbol, "market": market}
    except Exception as e:
        return {"success": False, "symbol": symbol, "error": f"行情失败: {e}"}


def run_live_check(
    symbol: str,
    *,
    amount: Optional[float] = None,
    exchange: Optional[str] = None,
    global_payout_today: Optional[float] = None,
    pool: Optional[float] = None,
    coverage: Optional[float] = None,
    solvency_ratio: Optional[float] = None,
    market: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    pool_v = pool if pool is not None else _pool_coverage()[0]
    cov_v = coverage if coverage is not None else _pool_coverage()[1]
    amt = amount if amount is not None else float(os.environ.get("WICKSHIELD_MONITOR_AMOUNT", "1000"))

    if solvency_ratio is None:
        solvency = SolvencyRiskManager.check_risk(pool_v, cov_v)
        ratio = float(solvency["data"]["ratio"]) if solvency.get("success") else 50.0
        solvency_data = solvency.get("data")
    else:
        ratio = float(solvency_ratio)
        solvency_data = None

    if market is None:
        fetched = fetch_monitor_market(symbol, exchange=exchange)
        if not fetched.get("success"):
            return fetched
        market = fetched["market"]

    amp = float(market.get("max_amplitude_1h_percent") or 0)
    threshold = float(get_threshold(symbol))

    premium = calc_premium(
        amount=amt,
        symbol=symbol,
        days=int(os.environ.get("WICKSHIELD_MONITOR_POLICY_DAYS", "7")),
        leverage=int(os.environ.get("WICKSHIELD_MONITOR_LEVERAGE", "10")),
        solvency_ratio=ratio,
        current_atr=market.get("current_atr_percent"),
        base_atr=market.get("base_atr_percent"),
        spike_count_1h=int(market.get("spike_count_1h") or 0),
        use_report_risk=True,
    )

    gpt = global_payout_today if global_payout_today is not None else 0.0
    payout = calc_payout(
        coverage=amt,
        symbol=symbol,
        actual_amplitude=amp,
        global_payout_today=gpt,
        solvency_ratio=ratio,
    )

    pd = payout.get("data") or {}
    pr = premium.get("data") or {}
    dynamic_cap = pr.get("dynamic_cap_ratio")
    if dynamic_cap is None and pr.get("cap_limit_percent") is not None:
        dynamic_cap = float(pr["cap_limit_percent"]) / 100.0

    triggered = amp >= threshold or bool(pd.get("is_full_payout"))
    decision = "no_trigger"
    reason = "振幅未达理赔阈值"

    if not payout.get("success"):
        decision = "error"
        reason = payout.get("error", "赔付计算失败")
    elif pd.get("blocked"):
        decision = "rejected"
        reason = pd.get("block_reason", "偿付能力不足，暂停赔付")
    elif not triggered:
        decision = "no_trigger"
    elif float(pd.get("final_payout") or 0) <= 0:
        decision = "rejected"
        if pd.get("is_limited_by_global_cap"):
            reason = "24h 全局限额已用尽"
        else:
            reason = "赔付金额为 0（单人上限或偿付折减）"
    else:
        decision = "approved"
        reason = "满足插针触发且额度充足"

    claim_verify: Optional[Dict[str, Any]] = None
    if decision == "approved" and market.get("light_mode"):
        from .claim_verify import verify_claim_before_payout

        claim_verify = verify_claim_before_payout(symbol, market, float(threshold))
        if claim_verify.get("success") and not claim_verify.get("verified"):
            decision = "rejected"
            reason = f"赔付前复核未通过: {claim_verify.get('reason', '全量未确认')}"

    return {
        "success": True,
        "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "symbol": symbol,
        "market": market,
        "claim_verify": claim_verify,
        "solvency": solvency_data,
        "solvency_ratio": ratio,
        "premium": pr,
        "payout": pd,
        "dynamic_cap_ratio": dynamic_cap,
        "global_cap_remaining": pd.get("global_cap_remaining"),
        "cap_24h": pd.get("cap_24h"),
        "triggered": triggered,
        "threshold_percent": threshold,
        "decision": decision,
        "reason": reason,
        "approved_payout": float(pd.get("final_payout") or 0) if decision == "approved" else 0.0,
    }


def _monitor_parallel_workers() -> int:
    from .fetch_guard import dynamic_monitor_workers

    return dynamic_monitor_workers()


def _fetch_markets_parallel(symbols: List[str]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    """并行拉取各币种行情；理赔决策仍在主线程串行（保证 global_payout 累计正确）。"""
    markets: Dict[str, Dict[str, Any]] = {}
    errors: Dict[str, str] = {}
    workers = min(_monitor_parallel_workers(), len(symbols) or 1)
    if not symbols:
        return markets, errors

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(fetch_monitor_market, sym): sym for sym in symbols}
        for fut in as_completed(futs):
            sym = futs[fut]
            res = fut.result()
            if res.get("success") and res.get("market"):
                markets[sym] = res["market"]
            else:
                errors[sym] = res.get("error", "行情失败")

    return markets, errors


def run_monitor_cycle(dry_run: bool = False) -> Dict[str, Any]:
    """扫描监控列表，更新状态并写入理赔日志。"""
    from .fetch_guard import get_round_metrics, reset_round_metrics

    reset_round_metrics()
    t0 = time.perf_counter()
    t_fetch0 = time.perf_counter()
    st = _load_state()
    gpt = float(st.get("global_payout_today") or 0)
    results: List[Dict[str, Any]] = []

    symbols = _watch_symbols()
    pool_v, cov_v = _pool_coverage()
    solvency = SolvencyRiskManager.check_risk(pool_v, cov_v)
    ratio = float(solvency["data"]["ratio"]) if solvency.get("success") else 50.0

    fetched, fetch_errors = _fetch_markets_parallel(symbols)
    fetch_ms = int((time.perf_counter() - t_fetch0) * 1000)
    t_compute0 = time.perf_counter()

    for symbol in symbols:
        if symbol not in fetched:
            err = fetch_errors.get(symbol, "行情未返回")
            results.append({"success": False, "symbol": symbol, "error": err})
            continue

        chk = run_live_check(
            symbol,
            global_payout_today=gpt,
            pool=pool_v,
            coverage=cov_v,
            solvency_ratio=ratio,
            market=fetched[symbol],
        )
        if not chk.get("success"):
            results.append(chk)
            continue

        entry = {
            "ts": chk["checked_at"],
            "symbol": symbol,
            "decision": chk["decision"],
            "reason": chk["reason"],
            "amplitude": chk["market"].get("max_amplitude_1h_percent"),
            "final_payout": chk["payout"].get("final_payout"),
            "global_cap_remaining_before": chk["payout"].get("global_cap_remaining"),
            "solvency_ratio": chk["solvency_ratio"],
            "dynamic_cap_ratio": chk.get("dynamic_cap_ratio"),
            "dry_run": dry_run,
        }

        if chk["decision"] == "approved" and not dry_run:
            paid = float(chk["approved_payout"])
            gpt += paid
            st["global_payout_today"] = gpt
            entry["global_payout_today_after"] = gpt
            _notify_chain({**entry, "approved_payout": paid})

        st.setdefault("claims", [])
        if chk["decision"] in ("approved", "rejected"):
            st["claims"].append(entry)
            st["claims"] = st["claims"][-200:]
            _append_claim_log(entry)

        st.setdefault("last_checks", {})[symbol] = chk["checked_at"]
        st.setdefault("last_live", {})[symbol] = live_snapshot_from_check(chk)
        results.append(chk)

    if not dry_run:
        _save_state(st)

    compute_ms = int((time.perf_counter() - t_compute0) * 1000)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    cache_info: Optional[Dict[str, Any]] = None
    redis_write_ms = 0
    if not dry_run:
        try:
            from .dashboard_data import build_dashboard_snapshot
            from .monitor_cache import save_monitor_snapshot

            t_cache0 = time.perf_counter()
            snap = build_dashboard_snapshot(live_refresh=False, skip_cache=True)
            cache_info = save_monitor_snapshot(snap)
            redis_write_ms = int((time.perf_counter() - t_cache0) * 1000)
        except Exception as e:
            cache_info = {"backend": "error", "error": str(e)}

    metrics = get_round_metrics()
    metrics.update(
        {
            "fetch_ohlcv_ms": fetch_ms,
            "compute_ms": compute_ms,
            "redis_write_ms": redis_write_ms,
            "total_ms": elapsed_ms,
        }
    )

    return {
        "success": True,
        "date": st.get("date"),
        "global_payout_today": gpt,
        "symbols_checked": len(results),
        "parallel_workers": _monitor_parallel_workers(),
        "ohlcv_workers": _monitor_parallel_workers(),
        "monitor_days_back": _monitor_market_kwargs()["days_back"],
        "duration_ms": elapsed_ms,
        "optimization_metrics": metrics,
        "cache": cache_info,
        "results": results,
    }
