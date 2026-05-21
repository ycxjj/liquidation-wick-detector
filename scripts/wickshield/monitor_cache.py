"""Monitor 结果缓存：优先 Redis，无 Redis 时用进程内 TTL。"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Optional

_mem: Dict[str, Any] = {"payload": None, "ts": 0.0}


def cache_ttl_sec() -> int:
    return max(5, int(os.environ.get("WICKSHIELD_CACHE_TTL", "30")))


def _redis_client():
    host = os.environ.get("WICKSHIELD_REDIS_HOST", "").strip()
    if not host:
        return None
    try:
        import redis  # type: ignore

        port = int(os.environ.get("WICKSHIELD_REDIS_PORT", "6379"))
        return redis.Redis(host=host, port=port, decode_responses=True, socket_timeout=2)
    except Exception:
        return None


def _snapshot_has_dashboard_fields(payload: Dict[str, Any]) -> bool:
    required = (
        "solvency_ratio",
        "global_cap_remaining",
        "cap_24h",
        "watch_symbols",
        "state_date",
    )
    return all(k in payload for k in required)


def save_monitor_snapshot(payload: Dict[str, Any]) -> Dict[str, Any]:
    """写入全量 monitor/看板快照（不完整 payload 仅写内存调试，不写 Redis）。"""
    if not _snapshot_has_dashboard_fields(payload):
        return {"backend": "skipped", "reason": "incomplete_snapshot", "ttl_sec": cache_ttl_sec()}
    ttl = cache_ttl_sec()
    body = json.dumps(payload, ensure_ascii=False)
    backend = "memory"
    r = _redis_client()
    if r is not None:
        try:
            r.setex("wickshield:dashboard", ttl, body)
            backend = "redis"
        except Exception:
            pass
    _mem["payload"] = payload
    _mem["ts"] = time.time()
    return {"backend": backend, "ttl_sec": ttl}


def clear_monitor_snapshot() -> None:
    """清空 monitor/看板缓存（测试或强制刷新用）。"""
    _mem["payload"] = None
    _mem["ts"] = 0.0
    r = _redis_client()
    if r is not None:
        try:
            r.delete("wickshield:dashboard")
        except Exception:
            pass


def load_monitor_snapshot() -> tuple[Optional[Dict[str, Any]], bool]:
    """返回 (snapshot, cache_hit)。"""
    ttl = cache_ttl_sec()
    r = _redis_client()
    if r is not None:
        try:
            raw = r.get("wickshield:dashboard")
            if raw:
                payload = json.loads(raw)
                if isinstance(payload, dict) and _snapshot_has_dashboard_fields(payload):
                    return payload, True
                return None, False
        except Exception:
            pass
    if _mem.get("payload") and time.time() - float(_mem.get("ts") or 0) <= ttl:
        payload = _mem["payload"]
        if isinstance(payload, dict) and _snapshot_has_dashboard_fields(payload):
            return payload, True
        return None, False
    return None, False
