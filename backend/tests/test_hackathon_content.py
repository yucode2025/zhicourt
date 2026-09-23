"""知乎黑客松匿名内容入口测试。"""
from __future__ import annotations

import pytest

from app.services.zhihu.hackathon import (
    HACKATHON_BASE_URL,
    HACKATHON_LIST_PATHS,
    HackathonContentError,
    _fetch,
    normalize_content_item,
)


def test_normalize_content_item_filters_and_bounds_untrusted_fields():
    item = normalize_content_item({
        "work_id": 1747681485547843585,
        "title": "  一条知识  ",
        "description": "简介\x00正文",
        "labels": ["科技", 3, "超长" * 20],
        "artwork": "javascript:alert(1)",
        "tab_artwork": "https://pic1.zhimg.com/a.png",
    })
    assert item is not None
    assert item["work_id"] == "1747681485547843585"
    assert item["title"] == "一条知识"
    assert item["description"] == "简介正文"
    assert item["labels"] == ["科技", "超长" * 15]
    assert item["artwork"] == ""
    assert item["tab_artwork"] == "https://pic1.zhimg.com/a.png"
    assert normalize_content_item({
        "work_id": "2", "title": "x", "artwork": "https://attacker.example/a.png",
    })["artwork"] == ""
    assert normalize_content_item({"work_id": "../bad", "title": "x"}) is None
    assert normalize_content_item({"work_id": None, "title": "x"}) is None
    assert normalize_content_item({"work_id": "1"}) is None


@pytest.mark.asyncio
async def test_fetch_uses_fixed_anonymous_endpoint_without_credentials(monkeypatch):
    seen: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            seen["client"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    async def fake_request_json(client, method, url, **kwargs):
        seen.update({"method": method, "url": url, **kwargs})
        return FakeResponse(), [{"work_id": "1", "title": "真实条目", "labels": []}]

    monkeypatch.setattr("app.services.zhihu.hackathon.httpx.AsyncClient", FakeClient)
    monkeypatch.setattr("app.services.zhihu.hackathon.request_json", fake_request_json)
    monkeypatch.setattr("app.services.zhihu.hackathon.validate_fixed_https_service", lambda url, **_kwargs: url)
    items = await _fetch("knowledge")

    assert items[0]["title"] == "真实条目"
    assert seen["url"] == HACKATHON_BASE_URL + HACKATHON_LIST_PATHS["knowledge"]
    assert seen["headers"] == {"Accept": "application/json"}
    assert seen["max_bytes"] == 1024 * 1024
    assert seen["client"]["follow_redirects"] is False
    assert seen["client"]["trust_env"] is False


def test_hackathon_content_route_success_and_honest_failure(client, monkeypatch):
    async def ok(kind, limit):
        return [{"work_id": "1", "title": f"{kind}-{limit}", "description": "", "labels": [], "artwork": "", "tab_artwork": ""}]

    monkeypatch.setattr("app.api.routes_cases.list_hackathon_content", ok)
    response = client.get("/api/hackathon/content?kind=story&limit=2")
    assert response.status_code == 200
    assert response.json()["items"][0]["title"] == "story-2"

    async def fail(kind, limit):
        raise HackathonContentError("上游暂时不可用")

    monkeypatch.setattr("app.api.routes_cases.list_hackathon_content", fail)
    failed = client.get("/api/hackathon/content")
    assert failed.status_code == 200
    assert failed.json() == {"kind": "knowledge", "items": [], "error": "上游暂时不可用"}


def test_hackathon_content_route_validates_kind_and_limit(client):
    assert client.get("/api/hackathon/content?kind=other").status_code == 422
    assert client.get("/api/hackathon/content?limit=21").status_code == 422


@pytest.mark.asyncio
async def test_hackathon_failure_is_cooled_down(monkeypatch):
    from app.services.cache.cache import _memory
    from app.services.zhihu import hackathon

    _memory._data.clear()
    calls = 0

    async def fail(_kind):
        nonlocal calls
        calls += 1
        raise HackathonContentError("upstream failed")

    monkeypatch.setattr(hackathon, "_fetch", fail)
    with pytest.raises(HackathonContentError, match="upstream failed"):
        await hackathon.list_hackathon_content("story")
    with pytest.raises(HackathonContentError, match="稍后再试"):
        await hackathon.list_hackathon_content("story")
    assert calls == 1
