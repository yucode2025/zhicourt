"""缓存服务测试（内存降级路径）。"""
from __future__ import annotations

from app.services.cache import cache_get, cache_incr, cache_set, make_key


def test_cache_roundtrip():
    key = make_key("test", {"q": "hello"})
    assert cache_get(key) is None
    cache_set(key, {"a": 1}, ttl_seconds=60)
    assert cache_get(key) == {"a": 1}


def test_cache_key_stable():
    assert make_key("a", 1) == make_key("a", 1)
    assert make_key("a", 1) != make_key("a", 2)


def test_cache_incr():
    key = make_key("ratelimit", "test-incr")
    n1 = cache_incr(key, 60)
    n2 = cache_incr(key, 60)
    assert n2 == n1 + 1
