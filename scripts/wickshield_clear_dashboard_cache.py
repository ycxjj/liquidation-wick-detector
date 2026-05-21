#!/usr/bin/env python3
"""清除 WickShield 看板 Redis/内存缓存（兼容旧版 monitor_cache，无 clear_monitor_snapshot 亦可）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _clear_redis() -> bool:
    host = os.environ.get("WICKSHIELD_REDIS_HOST", "").strip()
    if not host:
        return False
    try:
        import redis  # type: ignore

        port = int(os.environ.get("WICKSHIELD_REDIS_PORT", "6379"))
        r = redis.Redis(host=host, port=port, decode_responses=True, socket_timeout=2)
        r.delete("wickshield:dashboard")
        return True
    except Exception:
        return False


def _clear_memory_module() -> bool:
    try:
        from scripts.wickshield import monitor_cache as mc
    except Exception:
        return False
    if hasattr(mc, "clear_monitor_snapshot"):
        mc.clear_monitor_snapshot()
        return True
    if hasattr(mc, "_mem"):
        mc._mem["payload"] = None
        mc._mem["ts"] = 0.0
        return True
    return False


def main() -> int:
    redis_ok = _clear_redis()
    mem_ok = _clear_memory_module()
    print(
        "ok: cache cleared",
        f"redis={redis_ok}",
        f"memory={mem_ok}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
