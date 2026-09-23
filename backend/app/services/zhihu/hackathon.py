"""知乎黑客松配套故事/知识列表（活动接口，无需鉴权）。"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from app.services.cache import cache_get, cache_set
from app.services.outbound_security import request_json, validate_fixed_https_service

HackathonContentKind = Literal["knowledge", "story"]

HACKATHON_BASE_URL = "https://api.zhihu.com"
HACKATHON_LIST_PATHS: dict[HackathonContentKind, str] = {
    "knowledge": "/km-indep-home/hackathon/v2/knowledge/list",
    "story": "/km-indep-home/hackathon/v2/story/list",
}
_CACHE_TTL_SECONDS = 30 * 60
_FAILURE_CACHE_TTL_SECONDS = 60
_MAX_RESPONSE_BYTES = 1024 * 1024
_REQUEST_DEADLINE_SECONDS = 15.0
_OFFICIAL_IMAGE_SUFFIXES = (".zhimg.com", ".zhihu.com")
_locks = {kind: asyncio.Lock() for kind in HACKATHON_LIST_PATHS}


class HackathonContentError(RuntimeError):
    """活动内容接口不可用或返回了不符合约定的内容。"""


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    # 避免把上游控制字符带进日志、DOM 或复制内容；保留正常换行和制表符。
    return "".join(ch for ch in value.strip() if ch in "\n\t" or ord(ch) >= 32)[:limit]


def _official_image_url(value: Any) -> str:
    """活动图片只允许知乎官方 HTTPS 域；当前前端未使用，仍在 API 边界收紧。"""
    raw = _text(value, 2000)
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme != "https"
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or not any(host == suffix[1:] or host.endswith(suffix) for suffix in _OFFICIAL_IMAGE_SUFFIXES)
        ):
            return ""
    except ValueError:
        return ""
    return raw


def normalize_content_item(raw: Any) -> dict[str, Any] | None:
    """把活动接口的宽松对象收敛为首页所需的安全字段。"""
    if not isinstance(raw, dict):
        return None
    raw_work_id = raw.get("work_id")
    if isinstance(raw_work_id, bool) or not isinstance(raw_work_id, (str, int)):
        return None
    work_id = _text(str(raw_work_id), 80)
    title = _text(raw.get("title"), 300)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", work_id) or not title:
        return None
    labels = raw.get("labels")
    safe_labels = (
        [_text(label, 30) for label in labels[:8] if isinstance(label, str) and _text(label, 30)]
        if isinstance(labels, list)
        else []
    )
    return {
        "work_id": work_id,
        "title": title,
        "description": _text(raw.get("description"), 800),
        "labels": safe_labels,
        "artwork": _official_image_url(raw.get("artwork")),
        "tab_artwork": _official_image_url(raw.get("tab_artwork")),
    }


async def _fetch(kind: HackathonContentKind) -> list[dict[str, Any]]:
    path = HACKATHON_LIST_PATHS[kind]
    try:
        url = validate_fixed_https_service(
            f"{HACKATHON_BASE_URL}{path}", origin=HACKATHON_BASE_URL
        )
        timeout = httpx.Timeout(connect=5.0, read=8.0, write=5.0, pool=5.0)
        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=False, trust_env=False
        ) as client:
            response, body = await asyncio.wait_for(
                request_json(
                    client,
                    "GET",
                    url,
                    headers={"Accept": "application/json"},
                    expected_type=list,
                    max_bytes=_MAX_RESPONSE_BYTES,
                ),
                timeout=_REQUEST_DEADLINE_SECONDS,
            )
        response.raise_for_status()
    except (asyncio.TimeoutError, httpx.HTTPError, ValueError, TypeError) as exc:
        raise HackathonContentError(f"知乎黑客松内容暂时不可用（{type(exc).__name__}）") from None

    if not isinstance(body, list):
        raise HackathonContentError("知乎黑客松内容响应格式异常")
    items = [item for raw in body[:100] if (item := normalize_content_item(raw)) is not None]
    if not items:
        raise HackathonContentError("知乎黑客松内容暂时为空")
    return items


async def list_hackathon_content(kind: HackathonContentKind, limit: int = 12) -> list[dict[str, Any]]:
    """读取带缓存的活动列表；失败短暂冷却，避免公开入口放大上游故障。"""
    bounded_limit = max(1, min(int(limit), 20))
    cache_key = f"zhihu:hackathon:v2:{kind}"
    failure_key = f"{cache_key}:failure"
    cached = cache_get(cache_key)
    if isinstance(cached, list):
        return cached[:bounded_limit]
    if cache_get(failure_key):
        raise HackathonContentError("知乎黑客松内容暂时不可用，请稍后再试")

    async with _locks[kind]:
        cached = cache_get(cache_key)
        if isinstance(cached, list):
            return cached[:bounded_limit]
        if cache_get(failure_key):
            raise HackathonContentError("知乎黑客松内容暂时不可用，请稍后再试")
        try:
            items = await _fetch(kind)
        except HackathonContentError:
            cache_set(failure_key, True, _FAILURE_CACHE_TTL_SECONDS)
            raise
        cache_set(cache_key, items, _CACHE_TTL_SECONDS)
        return items[:bounded_limit]
