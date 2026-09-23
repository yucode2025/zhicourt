"""知乎官方 API 真实 Provider（按官方 http-api.md 规范实现）。

官方规范要点（官方 skill 包 zhihu-cli 0.5.3 references/http-api.md 核验）：
- Base URL：https://developer.zhihu.com
- 鉴权：Authorization: Bearer <Access Secret> + X-Request-Timestamp（秒级 Unix）+ Content-Type: application/json
- 知乎搜索：GET /api/v1/content/zhihu_search?Query=&Count=(<=10)
- 全网搜索：GET /api/v1/content/global_search?Query=&Count=(<=20)&SearchDB=all
- 热榜：    GET /api/v1/content/hot_list?Limit=(<=30)
- 直答：    POST /v1/chat/completions（OpenAI 兼容，model=zhida-fast-1p5 等）
- 额度：    GET /api/v1/quota?APIIDs=...（查询不消耗业务额度）
- 响应：{Code, Message, Data}；Code 0 成功 / 10001 参数 / 20001 鉴权 / 30001 频率 / 90001 内部
- 另有黑客松故事/知识接口（无需鉴权），见 hackathon.py
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.core.url_guard import UnsafeURLError, normalize_provider_url
from app.services import api_usage
from app.services.outbound_security import (
    DEFAULT_MAX_RESPONSE_BYTES,
    OutboundSecurityError,
    request_json,
    validate_outbound_url,
)
from app.services.zhihu.schemas import (
    NormalizedDirectAnswer,
    NormalizedHotItem,
    NormalizedSearchResult,
)

logger = get_logger(__name__)

MAX_RESPONSE_BYTES = DEFAULT_MAX_RESPONSE_BYTES

OFFICIAL_BASE_URL = "https://developer.zhihu.com"
PATH_ZHIHU_SEARCH = "/api/v1/content/zhihu_search"
PATH_GLOBAL_SEARCH = "/api/v1/content/global_search"
PATH_HOT_LIST = "/api/v1/content/hot_list"
PATH_ZHIDA = "/v1/chat/completions"
PATH_QUOTA = "/api/v1/quota"
PATH_KNOWLEDGE_SEARCH = "/api/v1/knowledge/search"


def validate_api_path(path: str) -> str:
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or any(
        mark in path for mark in ("?", "#", "\\", "\r", "\n")
    ) or any(part in (".", "..") for part in path.split("/")):
        raise ValueError("Provider 路径必须是站内绝对路径，不能包含查询、跳转或相对路径")
    return path


class ZhihuProviderError(Exception):
    def __init__(self, message: str, status_code: int | None = None, code: int | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


CODE_MESSAGES = {
    10001: "参数错误",
    20001: "鉴权失败（Access Secret 不正确）",
    30001: "触发频率限制",
    90001: "知乎服务内部错误",
}


def _pick(item: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for k in keys:
        if k in item and item[k] not in (None, ""):
            return item[k]
    return default


class _OfficialClient:
    """官方 API 公共客户端：GET + Bearer + X-Request-Timestamp + 指数退避重试。"""

    is_demo = False

    def __init__(self, api_key: str, base_url: str = "", paths: dict[str, str] | None = None) -> None:
        self.api_key = api_key
        self.base_url = normalize_provider_url(base_url or OFFICIAL_BASE_URL)
        self.paths = paths or {}

    def _safe_url(self, path: str) -> str:
        """每次出站前重新校验目标；API path 只能是站内绝对路径。"""
        try:
            validate_api_path(path)
        except ValueError:
            raise ZhihuProviderError("Provider 路径不合法") from None
        # 官方默认 origin 是代码常量，且 path 已收敛为站内绝对路径。跳过 DNS 私网
        # 判定以兼容透明代理 Fake-IP；后台自定义 origin 仍执行完整 SSRF 校验。
        if self.base_url == OFFICIAL_BASE_URL:
            return f"{OFFICIAL_BASE_URL}{path}"
        try:
            return validate_outbound_url(
                f"{self.base_url}{path}", allowed_origins=[self.base_url]
            )
        except (UnsafeURLError, OutboundSecurityError) as exc:
            raise ZhihuProviderError(f"目标地址不允许访问：{exc}") from None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "X-Request-Timestamp": str(int(time.time())),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _request(
        self,
        provider_name: str,
        path: str,
        params: dict[str, Any],
        *,
        timeout_seconds: float = 30.0,
        attempts: int = 3,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        attempts = max(1, attempts)
        for attempt in range(attempts):
            try:
                url = self._safe_url(path)
                async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False) as client:
                    resp, data = await request_json(
                        client, "GET", url, params=params, headers=self._headers(),
                        max_bytes=MAX_RESPONSE_BYTES,
                    )
                if resp.status_code in (401, 403):
                    raise ZhihuProviderError("鉴权失败（Access Secret 不正确）", status_code=resp.status_code, code=20001)
                if resp.status_code == 429:
                    raise ZhihuProviderError("触发频率限制", status_code=429, code=30001)
                resp.raise_for_status()
                if data is None:
                    raise ZhihuProviderError("Provider 响应为空")
                code = data.get("Code")
                if code not in (None, 0):
                    api_usage.record(provider_name, path, failure=True)
                    raise ZhihuProviderError(
                        CODE_MESSAGES.get(code, f"Provider 返回错误（代码 {code}）"),
                        status_code=resp.status_code, code=code,
                    )
                api_usage.record(provider_name, path)
                return data.get("Data") or {}
            except ZhihuProviderError:
                raise
            except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.HTTPError, OutboundSecurityError, ValueError, KeyError, TypeError) as exc:
                last_error = exc
                api_usage.record(provider_name, path, failure=True)
                if attempt < attempts - 1:
                    await asyncio.sleep(0.8 * (2**attempt))
        raise ZhihuProviderError(f"请求失败：{type(last_error).__name__}")


def _map_item(item: dict[str, Any], origin: Literal["zhihu", "web"]) -> NormalizedSearchResult:
    """官方 Item 字段 → 归一化结果。精选评论并入摘要（保留原文，供 Evidence Agent 使用）。"""
    summary = str(_pick(item, "ContentText", default=""))
    comments = item.get("CommentInfoList") or []
    top = [str(c.get("Content", ""))[:120] for c in comments[:2] if isinstance(c, dict)]
    if top:
        summary = summary + "\n精选评论：" + " | ".join(top)
    edit_time = _pick(item, "EditTime", default=0)
    published = ""
    try:
        ts = int(edit_time)
        if ts > 0:
            published = time.strftime("%Y-%m-%d", time.gmtime(ts))
    except (TypeError, ValueError):
        published = ""
    level = _pick(item, "AuthorityLevel", default=None)
    try:
        level = int(level) if level is not None and 1 <= int(level) <= 5 else None
    except (TypeError, ValueError):
        level = None
    return NormalizedSearchResult(
        origin=origin,
        kind={"Answer": "answer", "Article": "article", "Question": "question"}.get(
            str(_pick(item, "ContentType", default="")), str(_pick(item, "ContentType", default="")).lower() or "answer"
        ),
        title=str(_pick(item, "Title", default=""))[:500],
        url=str(_pick(item, "Url", default="")),
        author=str(_pick(item, "AuthorName", default=""))[:100],
        summary=summary[:2000],
        published_at=published,
        vote_count=int(_pick(item, "VoteUpCount", default=0) or 0),
        comment_count=int(_pick(item, "CommentCount", default=0) or 0),
        official_authority_level=level,
    )


class RealZhihuProvider(_OfficialClient):
    """知乎站内搜索 / 热榜 / 直答（官方规范）。"""

    async def search(self, query: str, limit: int = 10) -> list[NormalizedSearchResult]:
        count = max(1, min(int(limit), 10))
        data = await self._request("zhihu_search", self.paths.get("zhihu_search_path", PATH_ZHIHU_SEARCH), {"Query": query, "Count": count})
        items = data.get("Items") or []
        return [_map_item(i, "zhihu") for i in items[:count] if isinstance(i, dict)]

    async def hot_list(self, limit: int = 20) -> list[NormalizedHotItem]:
        limit = max(1, min(int(limit), 30))
        data = await self._request("zhihu_hot", self.paths.get("zhihu_hot_path", PATH_HOT_LIST), {"Limit": limit})
        items = data.get("Items") or []
        out: list[NormalizedHotItem] = []
        for i in items[:limit]:
            if not isinstance(i, dict):
                continue
            out.append(
                NormalizedHotItem(
                    title=str(_pick(i, "Title", default=""))[:300],
                    url=str(_pick(i, "Url", default="")),
                    heat=0,  # 官方热榜不返回热度数值
                    excerpt=str(_pick(i, "Summary", default=""))[:300],
                )
            )
        return out

    async def direct_answer(self, question: str, model: str = "zhida-fast-1p5") -> NormalizedDirectAnswer:
        """知乎直答（OpenAI 兼容接口，非流式）。"""
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": question}],
            "stream": False,
        }
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                url = self._safe_url(self.paths.get("zhihu_direct_answer_path", PATH_ZHIDA))
                async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
                    resp, body = await request_json(
                        client, "POST", url, json=payload, headers=self._headers(),
                        max_bytes=MAX_RESPONSE_BYTES,
                    )
                resp.raise_for_status()
                if body is None:
                    raise ZhihuProviderError("直答响应为空")
                if body.get("error"):
                    raise ZhihuProviderError("直答 Provider 返回错误", code=90001)
                content = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
                api_usage.record("zhihu_direct_answer", PATH_ZHIDA)
                return NormalizedDirectAnswer(answer=str(content)[:5000])
            except ZhihuProviderError:
                raise
            except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.HTTPError, OutboundSecurityError, KeyError, IndexError) as exc:
                last_error = exc
                api_usage.record("zhihu_direct_answer", PATH_ZHIDA, failure=True)
                if attempt < 1:
                    await asyncio.sleep(1.0)
        raise ZhihuProviderError(f"直答请求失败：{type(last_error).__name__}")

    async def quota(self) -> list[dict[str, Any]]:
        """查询官方额度；管理页请求快速失败，避免拖住本地统计。"""
        data = await self._request(
            "quota", PATH_QUOTA, {}, timeout_seconds=4.0, attempts=1,
        )
        items = data.get("Data") if isinstance(data, dict) else data
        return [i for i in (items or []) if isinstance(i, dict)]

    async def knowledge_search(self, query: str, knowledge_base_ids: list[str], limit: int = 5) -> list[str]:
        """知识库 RAG 检索（需要用户提供 KnowledgeBaseIDs）。"""
        if not knowledge_base_ids:
            raise ZhihuProviderError("知识库不可用：未配置 KnowledgeBaseIDs allowlist，不参与案件证据")
        payload = {"Query": query, "KnowledgeBaseIDs": knowledge_base_ids[:10], "Limit": max(1, min(limit, 10))}
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                url = self._safe_url(self.paths.get("zhihu_knowledge_path", PATH_KNOWLEDGE_SEARCH))
                async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
                    resp, body = await request_json(
                        client, "POST", url, json=payload, headers=self._headers(),
                        max_bytes=MAX_RESPONSE_BYTES,
                    )
                resp.raise_for_status()
                if body is None:
                    raise ZhihuProviderError("知识库响应为空")
                if body.get("Code") not in (None, 0):
                    code = body.get("Code")
                    raise ZhihuProviderError(CODE_MESSAGES.get(code, f"Provider 返回错误（代码 {code}）"), code=code)
                api_usage.record("zhihu_knowledge", PATH_KNOWLEDGE_SEARCH)
                data = body.get("Data") or {}
                items = data.get("Items") if isinstance(data, dict) else None
                out: list[str] = []
                for i in items or []:
                    if isinstance(i, dict):
                        out.extend(str(x)[:500] for x in (i.get("Content") or []) if x)
                return out[:limit]
            except ZhihuProviderError:
                raise
            except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.HTTPError, OutboundSecurityError) as exc:
                last_error = exc
                api_usage.record("zhihu_knowledge", PATH_KNOWLEDGE_SEARCH, failure=True)
                if attempt < 1:
                    await asyncio.sleep(1.0)
        raise ZhihuProviderError(f"知识库检索失败：{type(last_error).__name__}")


class RealWebSearchProvider(_OfficialClient):
    """全网搜索（官方 global_search）。"""

    async def search(self, query: str, limit: int = 8) -> list[NormalizedSearchResult]:
        count = max(1, min(int(limit), 20))
        data = await self._request("web_search", self.paths.get("web_search_path", PATH_GLOBAL_SEARCH), {"Query": query, "Count": count, "SearchDB": "all"})
        items = data.get("Items") or []
        return [_map_item(i, "web") for i in items[:count] if isinstance(i, dict)]
