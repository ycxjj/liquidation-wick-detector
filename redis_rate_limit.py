"""
Redis 滑动窗口限流，多 Gunicorn worker 共享。
未设置 REDIS_URL 时使用进程内内存限流（与改前行为一致，适合单 worker 或未装 Redis）。
"""

import os
import time
from collections import defaultdict, deque

_MEMORY_BUCKETS = defaultdict(deque)
_REDIS_CLIENT = None
_REDIS_AVAILABLE = None


def _get_redis():
    global _REDIS_CLIENT, _REDIS_AVAILABLE
    if _REDIS_AVAILABLE is False:
        return None
    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT
    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        _REDIS_AVAILABLE = False
        return None
    try:
        import redis
        client = redis.from_url(url, decode_responses=True, socket_connect_timeout=1.5)
        client.ping()
        _REDIS_CLIENT = client
        _REDIS_AVAILABLE = True
        return client
    except Exception:
        _REDIS_AVAILABLE = False
        return None


def _memory_rate_limit(key: str, limit: int, window_seconds: int) -> bool:
    now = time.time()
    bucket = _MEMORY_BUCKETS[key]
    while bucket and now - bucket[0] > window_seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        return False
    bucket.append(now)
    return True


def _redis_rate_limit(key: str, limit: int, window_seconds: int) -> bool:
    client = _get_redis()
    if not client:
        return _memory_rate_limit(key, limit, window_seconds)

    redis_key = f"rl:{key}"
    now = time.time()
    window_start = now - window_seconds
    pipe = client.pipeline()
    pipe.zremrangebyscore(redis_key, 0, window_start)
    pipe.zcard(redis_key)
    pipe.zadd(redis_key, {str(now): now})
    pipe.expire(redis_key, window_seconds + 1)
    results = pipe.execute()
    count = results[1]
    if count >= limit:
        client.zrem(redis_key, str(now))
        return False
    return True


def rate_limit(key: str, limit: int, window_seconds: int) -> bool:
    """返回 True 表示允许请求，False 表示应拒绝（429）。"""
    return _redis_rate_limit(key, limit, window_seconds)


def backend_name() -> str:
    if _get_redis():
        return "redis"
    return "memory"
