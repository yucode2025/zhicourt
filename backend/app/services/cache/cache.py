"""缓存服务：Redis 优先，不可用时自动降级为进程内 TTL 缓存。

接口统一 get/set，调用方无感知。生产 Ubuntu 使用真实 Redis（localhost）。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

try:
    import redis as redis_lib

    _pool: redis_lib.ConnectionPool | None = None
except ImportError:  # pragma: no cover
    redis_lib = None
    _pool = None


def _get_redis():  # noqa: ANN202
    global _pool
    if redis_lib is None or not settings.redis_url:
        return None
    if _pool is None:
        try:
            _pool = redis_lib.ConnectionPool.from_url(
                settings.redis_url, socket_connect_timeout=1.5, socket_timeout=1.5, decode_responses=True
            )
            client = redis_lib.Redis(connection_pool=_pool)
            client.ping()
            logger.info("Redis cache connected: %s", settings.redis_url.split("@")[-1])
            return client
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis unavailable, using in-memory cache fallback: %s", type(exc).__name__)
            _pool = None
            return None
    try:
        client = redis_lib.Redis(connection_pool=_pool)
        client.ping()
        return client
    except Exception:  # noqa: BLE001
        return None


class _MemoryCache:
    def __init__(self) -> None:
        self._data: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            expires, value = item
            if expires < time.time():
                del self._data[key]
                return None
            return value

    def set(self, key: str, value: str, ttl: int) -> None:
        with self._lock:
            self._data[key] = (time.time() + ttl, value)
            if len(self._data) > 5000:
                # 简单清理过期项
                now = time.time()
                self._data = {k: v for k, v in self._data.items() if v[0] > now}

    def incr(self, key: str, ttl: int) -> int:
        with self._lock:
            item = self._data.get(key)
            if not item or item[0] < time.time():
                self._data[key] = (time.time() + ttl, "1")
                return 1
            val = int(item[1]) + 1
            self._data[key] = (item[0], str(val))
            return val


_memory = _MemoryCache()
_redis_client = None
_redis_retry_at = 0.0
_REDIS_RETRY_SECONDS = 10.0


def _mark_redis_failed() -> None:
    global _redis_client, _pool, _redis_retry_at
    _redis_client = None
    _pool = None
    _redis_retry_at = time.monotonic() + _REDIS_RETRY_SECONDS


def _client():  # noqa: ANN202
    global _redis_client, _redis_retry_at
    if _redis_client is not None:
        return _redis_client
    if time.monotonic() < _redis_retry_at:
        return None
    _redis_client = _get_redis()
    if _redis_client is None:
        _redis_retry_at = time.monotonic() + _REDIS_RETRY_SECONDS
    return _redis_client


def cache_get(key: str) -> Any | None:
    c = _client()
    if c is not None:
        try:
            raw = c.get(key)
        except Exception:  # noqa: BLE001
            _mark_redis_failed()
            raw = _memory.get(key)
    else:
        raw = _memory.get(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def cache_set(key: str, value: Any, ttl_seconds: int) -> None:
    raw = json.dumps(value, ensure_ascii=False)
    c = _client()
    if c is not None:
        try:
            c.setex(key, ttl_seconds, raw)
            return
        except Exception:  # noqa: BLE001
            _mark_redis_failed()
    _memory.set(key, raw, ttl_seconds)


def cache_incr(key: str, ttl_seconds: int) -> int:
    c = _client()
    if c is not None:
        try:
            pipe = c.pipeline()
            pipe.incr(key)
            pipe.expire(key, ttl_seconds, nx=True)
            return int(pipe.execute()[0])
        except Exception:  # noqa: BLE001
            _mark_redis_failed()
    return _memory.incr(key, ttl_seconds)


def make_key(*parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
