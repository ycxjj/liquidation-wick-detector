"""
Binance USDT-M 合约 K 线 WebSocket（P2 可选）。
设 WICKSHIELD_WS_ENABLE=1 后，为监控列表币种订阅 5m kline，减少 Binance REST 压力。
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, List, Optional

_lock = threading.Lock()
_bars: Dict[str, List[Dict[str, Any]]] = {}
_thread: Optional[threading.Thread] = None
_running = False


def _symbol_stream(symbol: str) -> str:
    base = symbol.split("/")[0].lower()
    return f"{base}usdt@kline_5m"


def start_binance_ws(symbols: List[str], *, timeframe: str = "5m") -> None:
    global _thread, _running
    if _running:
        return
    try:
        import websocket  # type: ignore
    except ImportError as e:
        raise RuntimeError("需要 websocket-client: pip install websocket-client") from e

    if timeframe != "5m":
        raise ValueError("当前 WS 仅实现 5m kline")

    streams = [_symbol_stream(s) for s in symbols[:50]]
    stream_path = "/".join(streams)
    url = f"wss://fstream.binance.com/stream?streams={stream_path}"

    def on_message(_ws: Any, message: str) -> None:
        try:
            payload = json.loads(message)
            data = payload.get("data") or payload
            k = data.get("k") or {}
            sym = str(k.get("s", "")).upper()
            if not sym.endswith("USDT"):
                return
            symbol = f"{sym[:-4]}/USDT"
            bar_ts = int(k["t"])
            bar = {
                "timestamp": pd_timestamp_ms(bar_ts),
                "open": float(k["o"]),
                "high": float(k["h"]),
                "low": float(k["l"]),
                "close": float(k["c"]),
                "volume": float(k["v"]),
                "_ts_ms": bar_ts,
            }
            with _lock:
                lst = _bars.setdefault(symbol, [])
                if lst and lst[-1].get("_ts_ms") == bar_ts:
                    lst[-1] = bar
                else:
                    lst.append(bar)
                if len(lst) > 120:
                    del lst[:-120]
        except Exception:
            return

    def on_error(_ws: Any, _err: Any) -> None:
        pass

    def run() -> None:
        global _running
        _running = True
        ws = websocket.WebSocketApp(url, on_message=on_message, on_error=on_error)
        ws.run_forever(ping_interval=20, ping_timeout=10)

    _thread = threading.Thread(target=run, name="wickshield-binance-ws", daemon=True)
    _thread.start()


def pd_timestamp_ms(ms: int) -> Any:
    import pandas as pd

    return pd.to_datetime(ms, unit="ms", utc=True)


def get_ws_bars(symbol: str) -> List[Dict[str, Any]]:
    with _lock:
        return list(_bars.get(symbol, []))


def ws_status() -> Dict[str, Any]:
    with _lock:
        return {
            "running": _running,
            "symbols": len(_bars),
            "bars_total": sum(len(v) for v in _bars.values()),
        }
