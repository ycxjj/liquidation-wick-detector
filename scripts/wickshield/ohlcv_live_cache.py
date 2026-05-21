"""
进程内 OHLCV 热缓存 + 可选 Binance WS 增量（P2）。
REST 拉取结果写入缓存；monitor 周期内命中可跳过重复请求。
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

_lock = threading.Lock()
_store: Dict[str, Dict[str, Any]] = {}
_ws_started = False


def _enabled() -> bool:
    return os.environ.get("WICKSHIELD_OHLCV_CACHE", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def cache_ttl_sec() -> float:
    return max(5.0, float(os.environ.get("WICKSHIELD_OHLCV_CACHE_TTL", "90")))


def _key(exchange: str, symbol: str, timeframe: str, days_back: float) -> str:
    return f"{exchange}|{symbol}|{timeframe}|{days_back}"


def get_cached_df(
    exchange: str, symbol: str, timeframe: str, days_back: float
) -> Optional[pd.DataFrame]:
    if not _enabled():
        return None
    k = _key(exchange, symbol, timeframe, days_back)
    with _lock:
        ent = _store.get(k)
    if not ent:
        return None
    if time.time() - float(ent.get("ts") or 0) > cache_ttl_sec():
        return None
    df = ent.get("df")
    if df is None or len(df) < 20:
        return None
    return df.copy()


def put_cached_df(
    exchange: str, symbol: str, timeframe: str, days_back: float, df: pd.DataFrame
) -> None:
    if not _enabled() or df is None or len(df) < 20:
        return
    k = _key(exchange, symbol, timeframe, days_back)
    with _lock:
        _store[k] = {"df": df.copy(), "ts": time.time(), "source": "rest"}
        if len(_store) > 800:
            oldest = min(_store.items(), key=lambda x: x[1]["ts"])[0]
            _store.pop(oldest, None)


def cache_stats() -> Dict[str, Any]:
    with _lock:
        now = time.time()
        fresh = sum(1 for v in _store.values() if now - v.get("ts", 0) <= cache_ttl_sec())
        return {"entries": len(_store), "fresh": fresh, "ttl_sec": cache_ttl_sec()}


def maybe_start_ws(symbols: List[str], timeframe: str = "5m") -> bool:
    global _ws_started
    if not os.environ.get("WICKSHIELD_WS_ENABLE", "0").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return False
    if _ws_started:
        return True
    try:
        from .ws_kline_feed import start_binance_ws

        start_binance_ws(symbols, timeframe=timeframe)
        _ws_started = True
        return True
    except Exception:
        return False


def merge_ws_into_df(symbol: str, df: pd.DataFrame, exchange: str = "binanceusdm") -> pd.DataFrame:
    """若 WS 有更新 K 线，合并进 DataFrame（仅 binanceusdm）。"""
    try:
        from .ws_kline_feed import get_ws_bars

        bars = get_ws_bars(symbol)
        if not bars:
            return df
        ws_df = pd.DataFrame(bars)
        if ws_df.empty:
            return df
        combined = pd.concat([df, ws_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["timestamp"], keep="last")
        combined = combined.sort_values("timestamp").reset_index(drop=True)
        return combined
    except Exception:
        return df
