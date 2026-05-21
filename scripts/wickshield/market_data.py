"""
WickShield 实时行情：复用 wick_detector_v4 的 REST K 线抓取与插针检测。
"""
from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]

from .constants import THRESHOLDS_PIPS


def _wick_detector():
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from wick_detector_v4 import LiquidationDetector, fast_fetch_ohlcv_rest

    return LiquidationDetector, fast_fetch_ohlcv_rest

# 与日报六所一致；实时默认「六所分别拉 K 线后取最严指标」
SIX_EXCHANGES = ("binanceusdm", "okx", "gate", "bybit", "bitget", "mexc")
# 单所回退模式时的尝试顺序（仅 WICKSHIELD_MULTI_EXCHANGE=0 时使用）
DEFAULT_EXCHANGES = SIX_EXCHANGES


def ohlcv_exchange_order() -> List[str]:
    raw = os.environ.get("WICKSHIELD_OHLCV_EXCHANGES", "").strip()
    if raw:
        return [x.strip() for x in raw.split(",") if x.strip()]
    return list(SIX_EXCHANGES)


def multi_exchange_enabled() -> bool:
    """默认开启六所聚合；设 WICKSHIELD_MULTI_EXCHANGE=0 则退回「第一家能用的单所」。"""
    return os.environ.get("WICKSHIELD_MULTI_EXCHANGE", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def monitor_days_back() -> float:
    """实时监控默认 K 线回溯天数（v2.0 默认 0.3≈7.2h，够 1h 振幅 + ATR14）。"""
    return max(0.1, float(os.environ.get("WICKSHIELD_MONITOR_DAYS_BACK", "0.3")))


def ohlcv_parallel_workers() -> int:
    from .fetch_guard import dynamic_ohlcv_workers

    return dynamic_ohlcv_workers()


def monitor_light_mode() -> bool:
    """轻量监控：跳过 wick_detector 全量插针打分，用 K 线振幅计数（默认开）。"""
    return os.environ.get("WICKSHIELD_MONITOR_LIGHT", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def get_wick_threshold_pct(symbol: str) -> float:
    pips = THRESHOLDS_PIPS.get(symbol, THRESHOLDS_PIPS["DEFAULT"])
    return float(pips) / 100.0


def _true_range_pct(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            (df["high"] - df["low"]) / df["close"] * 100,
            (df["high"] - prev_close).abs() / df["close"] * 100,
            (df["low"] - prev_close).abs() / df["close"] * 100,
        ],
        axis=1,
    ).max(axis=1)
    return tr


def compute_atr_percent(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    if df is None or len(df) < period + 1:
        return None
    tr = _true_range_pct(df)
    return float(tr.rolling(period).mean().iloc[-1])


def compute_base_atr_percent(df: pd.DataFrame, period: int = 14) -> Optional[float]:
    """较长样本上的 ATR 中位数，作为「平静期」基准波动。"""
    if df is None or len(df) < period + 5:
        return None
    tr = _true_range_pct(df)
    atr_series = tr.rolling(period).mean().dropna()
    if atr_series.empty:
        return None
    return float(atr_series.median())


def count_spikes_last_hours_fast(
    df: pd.DataFrame,
    symbol: str,
    hours: float = 1.0,
) -> int:
    """轻量插针计数：近 N 小时内振幅达阈值的 K 线根数（不做 wick 形态打分）。"""
    if df is None or df.empty:
        return 0
    df = df.copy()
    if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        df["timestamp"] = pd.to_datetime(df["timestamp"])
    cutoff = df["timestamp"].max() - timedelta(hours=hours)
    recent = df[df["timestamp"] >= cutoff]
    if recent.empty:
        return 0
    thresh = get_wick_threshold_pct(symbol) * 0.5
    amp = (recent["high"] - recent["low"]) / recent["open"].replace(0, float("nan")) * 100
    return int((amp >= max(thresh, 0.5)).sum())


def count_spikes_last_hours(
    df: pd.DataFrame,
    symbol: str,
    hours: float = 1.0,
    detector: Any = None,
    *,
    light: bool = False,
) -> int:
    if light:
        return count_spikes_last_hours_fast(df, symbol, hours=hours)
    if df is None or df.empty:
        return 0
    df = df.copy()
    if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        df["timestamp"] = pd.to_datetime(df["timestamp"])
    cutoff = df["timestamp"].max() - timedelta(hours=hours)
    recent = df[df["timestamp"] >= cutoff].copy()
    if recent.empty:
        return 0

    LiquidationDetector, _ = _wick_detector()
    det = detector or LiquidationDetector(exchange_name=None, symbol=symbol)
    thresh = get_wick_threshold_pct(symbol)
    scored = det.detect_wicks(
        recent,
        min_amplitude_pct=max(thresh * 0.5, 0.5),
        body_ratio_threshold=0.5,
        wick_ratio_threshold=5.0,
        rebound_threshold=0.7,
    )
    if scored is None or scored.empty or "wick_score" not in scored.columns:
        return 0
    return int((scored["wick_score"] >= 0.8).sum())


def max_amplitude_last_hours(df: pd.DataFrame, hours: float = 1.0) -> float:
    """近 N 小时最大 K 线振幅（%%），用于赔付试算。"""
    if df is None or df.empty:
        return 0.0
    df = df.copy()
    if not pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        df["timestamp"] = pd.to_datetime(df["timestamp"])
    cutoff = df["timestamp"].max() - timedelta(hours=hours)
    recent = df[df["timestamp"] >= cutoff]
    if recent.empty:
        return 0.0
    amp = (recent["high"] - recent["low"]) / recent["open"] * 100
    return float(amp.max())


def fetch_ohlcv_with_fallback(
    symbol: str,
    timeframe: str = "5m",
    days_back: float = 2.0,
    exchanges: Optional[List[str]] = None,
) -> Tuple[pd.DataFrame, str]:
    """依次尝试多家交易所 REST，返回 (df, 成功的交易所名)。"""
    _, fast_fetch_ohlcv_rest = _wick_detector()
    tried = list(exchanges or ohlcv_exchange_order())
    errors: List[str] = []
    from .ohlcv_live_cache import get_cached_df, merge_ws_into_df, put_cached_df

    for ex in tried:
        df = get_cached_df(ex, symbol, timeframe, days_back)
        if df is None:
            df = fast_fetch_ohlcv_rest(ex, symbol, timeframe=timeframe, days_back=days_back)
            if df is not None and len(df) >= 20:
                if ex == "binanceusdm":
                    df = merge_ws_into_df(symbol, df, ex)
                put_cached_df(ex, symbol, timeframe, days_back, df)
        if df is not None and len(df) >= 20:
            return df, ex
        errors.append(f"{ex}: empty or too short ({0 if df is None else len(df)} bars)")
    raise RuntimeError("无法从交易所获取 K 线: " + "; ".join(errors))


def _snapshot_from_df(
    df: pd.DataFrame,
    symbol: str,
    *,
    timeframe: str,
    atr_period: int,
    spike_hours: float,
    light: bool = False,
) -> Dict[str, Any]:
    last_ts = df["timestamp"].iloc[-1]
    if hasattr(last_ts, "isoformat"):
        last_ts_str = last_ts.isoformat()
    else:
        last_ts_str = str(last_ts)
    return {
        "bars": len(df),
        "last_price": float(df["close"].iloc[-1]),
        "last_candle_time": last_ts_str,
        "current_atr_percent": compute_atr_percent(df, atr_period),
        "base_atr_percent": compute_base_atr_percent(df, atr_period),
        "spike_count_1h": count_spikes_last_hours(
            df, symbol, hours=spike_hours, light=light
        ),
        "max_amplitude_1h_percent": max_amplitude_last_hours(df, hours=spike_hours),
    }


def _fetch_one_exchange_snapshot(
    ex: str,
    symbol: str,
    *,
    timeframe: str,
    days_back: float,
    atr_period: int,
    spike_hours: float,
    light: bool,
) -> tuple[str, Optional[Dict[str, Any]], Optional[str]]:
    from .fetch_guard import run_with_guard

    _, fast_fetch_ohlcv_rest = _wick_detector()

    def _do_fetch():
        from .fetch_guard import record_live_cache_hit
        from .ohlcv_live_cache import get_cached_df, merge_ws_into_df, put_cached_df

        cached = get_cached_df(ex, symbol, timeframe, days_back)
        if cached is not None:
            record_live_cache_hit()
            return cached
        df = fast_fetch_ohlcv_rest(ex, symbol, timeframe=timeframe, days_back=days_back)
        if df is not None and len(df) >= 20:
            if ex == "binanceusdm":
                df = merge_ws_into_df(symbol, df, ex)
            put_cached_df(ex, symbol, timeframe, days_back, df)
        return df

    df, err = run_with_guard(ex, symbol, timeframe, days_back, _do_fetch)
    if df is None:
        return ex, None, err or "拉取失败"
    if len(df) < 20:
        return ex, None, "K线不足"
    snap = _snapshot_from_df(
        df,
        symbol,
        timeframe=timeframe,
        atr_period=atr_period,
        spike_hours=spike_hours,
        light=light,
    )
    if err:
        snap["fetch_degraded"] = True
    return ex, snap, None


def _aggregate_multi_exchange(
    per_exchange: Dict[str, Dict[str, Any]],
    symbol: str,
    *,
    timeframe: str,
    errors: List[str],
) -> Dict[str, Any]:
    lead_ex = max(
        per_exchange.items(),
        key=lambda item: float(item[1].get("max_amplitude_1h_percent") or 0),
    )[0]
    lead = per_exchange[lead_ex]
    max_amp = max(float(v.get("max_amplitude_1h_percent") or 0) for v in per_exchange.values())
    max_spikes = max(int(v.get("spike_count_1h") or 0) for v in per_exchange.values())
    atr_vals = [
        v["current_atr_percent"]
        for v in per_exchange.values()
        if v.get("current_atr_percent") is not None
    ]
    base_vals = [
        v["base_atr_percent"]
        for v in per_exchange.values()
        if v.get("base_atr_percent") is not None
    ]
    current_atr = max(atr_vals) if atr_vals else None
    base_atr = float(pd.Series(base_vals).median()) if base_vals else None
    return {
        "success": True,
        "mode": "multi",
        "exchange": "multi",
        "lead_exchange": lead_ex,
        "exchanges_used": list(per_exchange.keys()),
        "exchanges_count": len(per_exchange),
        "per_exchange": per_exchange,
        "symbol": symbol,
        "timeframe": timeframe,
        "bars": lead.get("bars"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "last_price": lead.get("last_price"),
        "last_candle_time": lead.get("last_candle_time"),
        "current_atr_percent": current_atr,
        "base_atr_percent": base_atr,
        "spike_count_1h": max_spikes,
        "max_amplitude_1h_percent": max_amp,
        "wick_threshold_percent": get_wick_threshold_pct(symbol),
        "fetch_errors": errors or None,
    }


def build_market_snapshot(
    symbol: str,
    exchange: Optional[str] = None,
    timeframe: str = "5m",
    days_back: float = 2.0,
    atr_period: int = 14,
    spike_hours: float = 1.0,
    light: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    拉取实时 K 线并计算精算所需指标。
    未指定 exchange 且开启六所聚合时：与日报一致，对各所 K 线分别检测后取最大振幅/插针次数（最严）。
    """
    _, fast_fetch_ohlcv_rest = _wick_detector()
    use_light = monitor_light_mode() if light is None else bool(light)

    if exchange:
        df = fast_fetch_ohlcv_rest(exchange, symbol, timeframe=timeframe, days_back=days_back)
        if df is None or len(df) < 20:
            raise RuntimeError(f"{exchange} 返回 K 线不足 ({0 if df is None else len(df)} 根)")
        m = _snapshot_from_df(
            df,
            symbol,
            timeframe=timeframe,
            atr_period=atr_period,
            spike_hours=spike_hours,
            light=use_light,
        )
        return {
            "success": True,
            "mode": "single",
            "exchange": exchange,
            "symbol": symbol,
            "timeframe": timeframe,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "wick_threshold_percent": get_wick_threshold_pct(symbol),
            **m,
        }

    if not multi_exchange_enabled():
        df, used_exchange = fetch_ohlcv_with_fallback(symbol, timeframe, days_back)
        m = _snapshot_from_df(
            df,
            symbol,
            timeframe=timeframe,
            atr_period=atr_period,
            spike_hours=spike_hours,
            light=use_light,
        )
        return {
            "success": True,
            "mode": "single_fallback",
            "exchange": used_exchange,
            "symbol": symbol,
            "timeframe": timeframe,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "wick_threshold_percent": get_wick_threshold_pct(symbol),
            **m,
        }

    per_exchange: Dict[str, Dict[str, Any]] = {}
    errors: List[str] = []
    exchanges = ohlcv_exchange_order()
    workers = min(ohlcv_parallel_workers(), len(exchanges))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {
            pool.submit(
                _fetch_one_exchange_snapshot,
                ex,
                symbol,
                timeframe=timeframe,
                days_back=days_back,
                atr_period=atr_period,
                spike_hours=spike_hours,
                light=use_light,
            ): ex
            for ex in exchanges
        }
        for fut in as_completed(futs):
            ex, snap, err = fut.result()
            if snap:
                per_exchange[ex] = snap
            elif err:
                errors.append(f"{ex}: {err}")

    if not per_exchange:
        raise RuntimeError("六所均无可用 K 线: " + "; ".join(errors))

    out = _aggregate_multi_exchange(
        per_exchange, symbol, timeframe=timeframe, errors=errors
    )
    out["parallel_fetch"] = True
    out["light_mode"] = use_light
    return out
