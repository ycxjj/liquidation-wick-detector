"""
赔付前全量复核：轻量模式触发后，用 wick_detector 确认近 1h 存在合格插针。
"""
from __future__ import annotations

import os
from datetime import timedelta
from typing import Any, Dict, Optional

import pandas as pd


def claim_full_verify_enabled() -> bool:
    return os.environ.get("WICKSHIELD_CLAIM_FULL_VERIFY", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def verify_claim_before_payout(
    symbol: str,
    market: Dict[str, Any],
    threshold_percent: float,
    *,
    timeframe: Optional[str] = None,
    days_back: Optional[float] = None,
) -> Dict[str, Any]:
    """
    返回 success, verified, reason, full_spike_count, max_amplitude_percent, lead_exchange
    """
    from .market_data import (
        get_wick_threshold_pct,
        max_amplitude_last_hours,
        monitor_days_back,
        _wick_detector,
    )

    if not claim_full_verify_enabled():
        return {
            "success": True,
            "verified": True,
            "skipped": True,
            "reason": "复核已关闭",
        }

    lead = market.get("lead_exchange")
    if not lead or lead in ("multi", "single", "single_fallback"):
        used = market.get("exchanges_used") or []
        lead = used[0] if used else market.get("exchange")
    if not lead or lead == "multi":
        return {
            "success": False,
            "verified": False,
            "reason": "无法确定牵头交易所，跳过复核",
        }

    tf = timeframe or os.environ.get("WICKSHIELD_MONITOR_TIMEFRAME", "5m")
    db = days_back if days_back is not None else monitor_days_back()
    thresh = threshold_percent if threshold_percent > 0 else get_wick_threshold_pct(symbol)

    _, fast_fetch = _wick_detector()
    from .fetch_guard import run_with_guard

    def _fetch():
        return fast_fetch(lead, symbol, timeframe=tf, days_back=db)

    df, err = run_with_guard(lead, symbol, tf, db, _fetch)
    if df is None or (hasattr(df, "__len__") and len(df) < 20):
        return {
            "success": False,
            "verified": False,
            "reason": f"复核行情失败: {err or 'K线不足'}",
            "lead_exchange": lead,
        }

    amp = max_amplitude_last_hours(df, hours=1.0)
    if amp < thresh:
        return {
            "success": True,
            "verified": False,
            "reason": f"复核振幅 {amp:.2f}% 未达阈值 {thresh}%",
            "max_amplitude_percent": amp,
            "lead_exchange": lead,
        }

    LiquidationDetector, _ = _wick_detector()
    det = LiquidationDetector(exchange_name=lead, symbol=symbol)
    df = df.copy()
    if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        df["timestamp"] = pd.to_datetime(df["timestamp"])
    cutoff = df["timestamp"].max() - timedelta(hours=1.0)
    recent = df[df["timestamp"] >= cutoff].copy()
    if recent.empty:
        return {
            "success": True,
            "verified": False,
            "reason": "复核窗口无 K 线",
            "lead_exchange": lead,
        }

    scored = det.detect_wicks(
        recent,
        min_amplitude_pct=max(thresh * 0.5, 0.5),
        body_ratio_threshold=0.5,
        wick_ratio_threshold=5.0,
        rebound_threshold=0.7,
    )
    valid_count = 0
    best_amp = 0.0
    if scored is not None and not scored.empty and "wick_score" in scored.columns:
        for _, row in scored.iterrows():
            score = float(row.get("wick_score") or 0)
            row_amp = float(row.get("amplitude") or 0)
            if score >= 0.8 and row_amp >= thresh:
                valid_count += 1
                best_amp = max(best_amp, row_amp)

    if valid_count > 0:
        return {
            "success": True,
            "verified": True,
            "reason": "全量复核确认合格插针",
            "full_spike_count": valid_count,
            "max_amplitude_percent": max(amp, best_amp),
            "lead_exchange": lead,
        }

    return {
        "success": True,
        "verified": False,
        "reason": "轻量触发但全量形态未确认（疑似假阳性）",
        "max_amplitude_percent": amp,
        "full_spike_count": 0,
        "lead_exchange": lead,
    }
