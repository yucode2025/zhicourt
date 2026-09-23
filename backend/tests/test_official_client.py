"""官方 API 字段映射与错误码测试（按 zhihu skill 0.5.3 http-api.md）。"""
from __future__ import annotations

import time

import pytest

from app.services.zhihu.client import (
    CODE_MESSAGES,
    OFFICIAL_BASE_URL,
    PATH_GLOBAL_SEARCH,
    PATH_HOT_LIST,
    PATH_ZHIHU_SEARCH,
    RealZhihuProvider,
    RealWebSearchProvider,
    ZhihuProviderError,
    _map_item,
)


def test_official_paths_match_docs():
    assert PATH_ZHIHU_SEARCH == "/api/v1/content/zhihu_search"
    assert PATH_GLOBAL_SEARCH == "/api/v1/content/global_search"
    assert PATH_HOT_LIST == "/api/v1/content/hot_list"
    assert OFFICIAL_BASE_URL == "https://developer.zhihu.com"


def test_headers_carry_bearer_and_timestamp():
    p = RealZhihuProvider(api_key="sk-test")
    h = p._headers()
    assert h["Authorization"] == "Bearer sk-test"
    ts = int(h["X-Request-Timestamp"])
    assert abs(ts - time.time()) < 60  # 秒级时间戳
    assert h["Content-Type"] == "application/json"


def test_map_item_official_fields():
    item = {
        "Title": "RAG 评测方法综述",
        "ContentType": "Article",
        "ContentID": "123456789",
        "ContentText": "本文介绍了主流 RAG 评测框架...",
        "Url": "https://zhuanlan.zhihu.com/p/123456789?utm_medium=openapi_platform",
        "CommentCount": 15,
        "VoteUpCount": 128,
        "AuthorName": "张三",
        "EditTime": 1710000000,
        "CommentInfoList": [{"Content": "很有帮助"}, {"Content": "收藏了"}],
        "AuthorityLevel": "2",
        "RankingScore": 0.98,
    }
    r = _map_item(item, "zhihu")
    assert r.title == "RAG 评测方法综述"
    assert r.kind == "article"
    assert r.author == "张三"
    assert r.vote_count == 128
    assert r.comment_count == 15
    assert r.published_at == "2024-03-09"  # EditTime 1710000000 → UTC
    assert "精选评论" in r.summary and "很有帮助" in r.summary
    assert r.official_authority_level == 2


def test_map_item_defaults_missing_fields():
    r = _map_item({"Title": "仅标题"}, "web")
    assert r.title == "仅标题"
    assert r.url == ""
    assert r.vote_count == 0
    assert r.published_at == ""


def test_error_code_messages_official():
    assert CODE_MESSAGES[20001] == "鉴权失败（Access Secret 不正确）"
    assert CODE_MESSAGES[30001] == "触发频率限制"
    assert CODE_MESSAGES[10001] == "参数错误"
    err = ZhihuProviderError("x", code=20001)
    assert err.code == 20001


def test_provider_base_url_defaults_official():
    p = RealZhihuProvider(api_key="k")
    assert p.base_url == "https://developer.zhihu.com"
    w = RealWebSearchProvider(api_key="k", base_url="")
    assert w.base_url == "https://developer.zhihu.com"


@pytest.mark.asyncio
async def test_real_provider_validates_before_outbound(monkeypatch):
    provider = RealZhihuProvider(api_key="provider-secret", base_url="http://127.0.0.1:9000")
    opened = False

    class ShouldNotOpen:
        def __init__(self, *args, **kwargs):
            nonlocal opened
            opened = True

    monkeypatch.setattr("app.services.zhihu.client.httpx.AsyncClient", ShouldNotOpen)
    with pytest.raises(ZhihuProviderError) as exc_info:
        await provider.search("q", 1)
    assert opened is False
    assert "provider-secret" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_real_provider_disables_redirects(monkeypatch):
    provider = RealZhihuProvider(api_key="provider-secret", base_url="https://93.184.216.34")
    seen: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        headers = {"Content-Length": "30"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def aiter_bytes(self):
            yield b'{"Code":0,"Data":{"Items":[]}}'

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            seen.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def stream(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("app.services.zhihu.client.httpx.AsyncClient", FakeClient)
    assert await provider.search("q", 1) == []
    assert seen["follow_redirects"] is False


@pytest.mark.asyncio
async def test_quota_uses_short_timeout_without_retries(monkeypatch):
    provider = RealZhihuProvider(api_key="provider-secret")
    seen: dict[str, object] = {}

    async def fake_request(provider_name, path, params, **kwargs):
        seen.update({"provider_name": provider_name, "path": path, **kwargs})
        return []

    monkeypatch.setattr(provider, "_request", fake_request)
    assert await provider.quota() == []
    assert seen["provider_name"] == "quota"
    assert seen["timeout_seconds"] == 4.0
    assert seen["attempts"] == 1
