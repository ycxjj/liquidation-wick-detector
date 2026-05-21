"""
实时监控拉取防护：超时跳过、交易所熔断、限频、OHLCV 降级缓存。
"""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, TypeVar

T = TypeVar("T")

_lock = threading.Lock()
_circuit: Dict[str, Dict[str, Any]] = defaultdict(
    lambda: {"failures": 0, "open_until": 0.0}
)
_ohlcv_cache: Dict[str, Dict[str, Any]] = {}
_rate_limiters: Dict[str, "_TokenBucket"] = {}

# 本轮 monitor 累计指标（线程安全）
_metrics_lock = threading.Lock()
_round_metrics: Dict[str, Any] = {
    "timeout_count": 0,
    "circuit_skip_count": 0,
    "cache_fallback_count": 0,
    "rate_limit_waits_ms": 0,
    "rate_limit_hits": {},
    "exchange_errors": [],
}


def reset_round_metrics() -> None:
    with _metrics_lock:
        _round_metrics.clear()
        _round_metrics.update(
            {
                "timeout_count": 0,
                "circuit_skip_count": 0,
                "cache_fallback_count": 0,
                "rate_limit_waits_ms": 0,
                "rate_limit_hits": {},
                "exchange_errors": [],
            }
        )


def get_round_metrics() -> Dict[str, Any]:
    with _metrics_lock:
        out = dict(_round_metrics)
        out["rate_limit_hits"] = dict(out.get("rate_limit_hits") or {})
        out["exchange_errors"] = list(out.get("exchange_errors") or [])
        cb_active = [
            ex
            for ex, st in _circuit.items()
            if float(st.get("open_until") or 0) > time.time()
        ]
        out["circuit_breaker_active"] = cb_active
        return out


def _env_bool(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in ("0", "false", "no")


def request_timeout_sec() -> float:
    return max(1.0, float(os.environ.get("WICKSHIELD_REQUEST_TIMEOUT", "3")))


def skip_on_timeout() -> bool:
    return _env_bool("WICKSHIELD_SKIP_TIMEOUT", "1")


def circuit_breaker_enabled() -> bool:
    return _env_bool("WICKSHIELD_CIRCUIT_BREAKER", "1")


def rate_limit_enabled() -> bool:
    return _env_bool("WICKSHIELD_RATE_LIMIT_ENABLE", "1")


def circuit_fail_threshold() -> int:
    return max(1, int(os.environ.get("WICKSHIELD_CIRCUIT_FAIL_THRESHOLD", "3")))


def circuit_open_seconds() -> int:
    return max(30, int(os.environ.get("WICKSHIELD_CIRCUIT_OPEN_SEC", "300")))


def _exchange_rps(exchange: str) -> float:
    key = f"WICKSHIELD_{exchange.upper()}_RPS"
    defaults = {
        "binanceusdm": 20,
        "okx": 20,
        "bybit": 30,
        "gate": 15,
        "bitget": 20,
        "mexc": 20,
    }
    if key in os.environ:
        return max(1.0, float(os.environ[key]))
    return float(defaults.get(exchange, 20))


class _TokenBucket:
    def __init__(self, rate_per_sec: float) -> None:
        self.rate = rate_per_sec
        self.tokens = rate_per_sec
        self.last = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self) -> float:
        """阻塞直到拿到令牌，返回等待毫秒数。"""
        waited = 0.0
        while True:
            with self.lock:
                now = time.monotonic()
                elapsed = now - self.last
                self.tokens = min(self.rate, self.tokens + elapsed * self.rate)
                self.last = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return waited * 1000.0
                need = (1.0 - self.tokens) / self.rate
            time.sleep(min(need, 0.25))
            waited += need


def _get_limiter(exchange: str) -> _TokenBucket:
    with _lock:
        if exchange not in _rate_limiters:
            _rate_limiters[exchange] = _TokenBucket(_exchange_rps(exchange))
        return _rate_limiters[exchange]


def _is_circuit_open(exchange: str) -> bool:
    if not circuit_breaker_enabled():
        return False
    st = _circuit[exchange]
    return float(st.get("open_until") or 0) > time.time()


def _record_success(exchange: str) -> None:
    if not circuit_breaker_enabled():
        return
    with _lock:
        _circuit[exchange]["failures"] = 0
        _circuit[exchange]["open_until"] = 0.0


def _record_failure(exchange: str, err: str) -> None:
    if not circuit_breaker_enabled():
        return
    with _lock:
        st = _circuit[exchange]
        st["failures"] = int(st.get("failures") or 0) + 1
        if st["failures"] >= circuit_fail_threshold():
            st["open_until"] = time.time() + circuit_open_seconds()
            st["failures"] = 0
    with _metrics_lock:
        errs: List[str] = _round_metrics.setdefault("exchange_errors", [])
        if len(errs) < 50:
            errs.append(f"{exchange}: {err}")


def _cache_key(exchange: str, symbol: str, timeframe: str, days_back: float) -> str:
    return f"{exchange}:{symbol}:{timeframe}:{days_back}"


def _get_cached_ohlcv(key: str) -> Optional[Any]:
    ent = _ohlcv_cache.get(key)
    if not ent:
        return None
    ttl = float(os.environ.get("WICKSHIELD_OHLCV_CACHE_TTL", "120"))
    if time.time() - float(ent.get("ts") or 0) > ttl:
        return None
    return ent.get("df")


def _set_cached_ohlcv(key: str, df: Any) -> None:
    _ohlcv_cache[key] = {"df": df, "ts": time.time()}
    if len(_ohlcv_cache) > 500:
        oldest = min(_ohlcv_cache.items(), key=lambda x: x[1]["ts"])[0]
        _ohlcv_cache.pop(oldest, None)


def run_with_guard(
    exchange: str,
    symbol: str,
    timeframe: str,
    days_back: float,
    fetch_fn: Callable[[], T],
) -> tuple[Optional[T], Optional[str]]:
    """
    限频 → 熔断检查 → 超时执行 → 失败时读 OHLCV 缓存。
    返回 (result, error_message)。
    """
    if _is_circuit_open(exchange):
        with _metrics_lock:
            _round_metrics["circuit_skip_count"] = int(_round_metrics.get("circuit_skip_count") or 0) + 1
        cached = _get_cached_ohlcv(_cache_key(exchange, symbol, timeframe, days_back))
        if cached is not None:
            with _metrics_lock:
                _round_metrics["cache_fallback_count"] = int(
                    _round_metrics.get("cache_fallback_count") or 0
                ) + 1
            return cached, None
        return None, f"{exchange}: 熔断中且无缓存"

    if rate_limit_enabled():
        wait_ms = _get_limiter(exchange).acquire()
        if wait_ms > 5:
            with _metrics_lock:
                hits = _round_metrics.setdefault("rate_limit_hits", {})
                hits[exchange] = int(hits.get(exchange) or 0) + 1
                _round_metrics["rate_limit_waits_ms"] = int(
                    _round_metrics.get("rate_limit_waits_ms") or 0
                ) + int(wait_ms)

    key = _cache_key(exchange, symbol, timeframe, days_back)
    timeout = request_timeout_sec()
    from concurrent.futures import ThreadPoolExecutor

    err_msg: Optional[str] = None
    result: Optional[T] = None
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(fetch_fn)
            result = fut.result(timeout=timeout)
        _record_success(exchange)
        if result is not None:
            _set_cached_ohlcv(key, result)
        return result, None
    except Exception as e:
        err_msg = f"{type(e).__name__}: {e}"
        is_timeout = "Timeout" in type(e).__name__ or "timeout" in str(e).lower()
        if is_timeout:
            with _metrics_lock:
                _round_metrics["timeout_count"] = int(_round_metrics.get("timeout_count") or 0) + 1
        _record_failure(exchange, err_msg)

        cached = _get_cached_ohlcv(key)
        if cached is not None:
            with _metrics_lock:
                _round_metrics["cache_fallback_count"] = int(
                    _round_metrics.get("cache_fallback_count") or 0
                ) + 1
            return cached, None

        if is_timeout and skip_on_timeout():
            return None, f"{exchange}: 超时({timeout}s)"
        return None, err_msg


def dynamic_monitor_workers() -> int:
    """按小时动态调整 MONITOR/OHLCV worker（可被环境变量显式覆盖）。"""
    if os.environ.get("WICKSHIELD_MONITOR_WORKERS", "").strip():
        return max(1, min(int(os.environ["WICKSHIELD_MONITOR_WORKERS"]), 16))
    try:
        from zoneinfo import ZoneInfo

        hour = datetime.now(ZoneInfo(os.environ.get("DAILY_REPORT_TZ", "Asia/Shanghai"))).hour
    except Exception:
        hour = time.localtime().tm_hour

    if 1 <= hour <= 6:
        return 4
    if 9 <= hour <= 17:
        return 8
    return 6


def dynamic_ohlcv_workers() -> int:
    if os.environ.get("WICKSHIELD_OHLCV_WORKERS", "").strip():
        return max(1, min(int(os.environ["WICKSHIELD_OHLCV_WORKERS"]), 12))
    return min(dynamic_monitor_workers(), 12)
