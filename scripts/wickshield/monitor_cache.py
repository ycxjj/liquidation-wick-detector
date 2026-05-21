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


def save_monitor_snapshot(payload: Dict[str, Any]) -> Dict[str, Any]:
    """写入全量 monitor/看板快照。"""
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


def load_monitor_snapshot() -> tuple[Optional[Dict[str, Any]], bool]:
    """返回 (snapshot, cache_hit)。"""
    ttl = cache_ttl_sec()
    r = _redis_client()
    if r is not None:
        try:
            raw = r.get("wickshield:dashboard")
            if raw:
                return json.loads(raw), True
        except Exception:
            pass
    if _mem.get("payload") and time.time() - float(_mem.get("ts") or 0) <= ttl:
        return _mem["payload"], True
    return None, False
