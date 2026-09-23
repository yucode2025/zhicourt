"""共享出站 HTTP 安全：目标约束、流式限量读取与 JSON 结构校验。"""
from __future__ import annotations

import codecs
import json
import math
import re
from collections.abc import Iterable
from typing import Any

import httpx

from app.core.config import settings
from app.core.url_guard import ensure_safe_provider_url, normalize_provider_url, provider_origin

DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_JSON_DEPTH = 32
DEFAULT_MAX_JSON_NODES = 50_000


class OutboundSecurityError(ValueError):
    """响应或目标违反出站安全边界。"""


class SSEInterruptedError(OutboundSecurityError):
    """The upstream stream ended without a complete answer; safe to retry."""


class SSECompletionError(OutboundSecurityError):
    """A complete upstream event explicitly says the answer was unusable."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"SSE 回答未正常完成：{reason}")
        self.reason = reason


def validate_outbound_url(url: str, *, allowed_origins: Iterable[str]) -> str:
    """重新解析/DNS 校验 URL，并只允许调用方声明的精确 origin。"""
    safe_url = ensure_safe_provider_url(url)
    origin = provider_origin(safe_url)
    allowlist = {provider_origin(item) for item in allowed_origins}
    if origin not in allowlist:
        raise OutboundSecurityError("出站目标 Origin 不在 allowlist")
    if settings.is_prod and not safe_url.startswith("https://"):
        raise OutboundSecurityError("生产环境 Provider 必须使用 HTTPS")
    return safe_url


def validate_fixed_https_service(url: str, *, origin: str) -> str:
    """校验代码内固定、无凭据的 HTTPS 服务；不接受配置值，也不做 Fake-IP DNS 判定。"""
    safe_url = normalize_provider_url(url)
    safe_origin = provider_origin(origin)
    if not safe_origin.startswith("https://") or provider_origin(safe_url) != safe_origin:
        raise OutboundSecurityError("固定服务目标不符合 HTTPS Origin 契约")
    return safe_url


def _content_length(response: httpx.Response) -> int | None:
    value = response.headers.get("content-length")
    if value is None:
        return None
    try:
        length = int(value)
    except (TypeError, ValueError) as exc:
        raise OutboundSecurityError("响应 Content-Length 不合法") from exc
    if length < 0:
        raise OutboundSecurityError("响应 Content-Length 不合法")
    return length


def _check_json_nesting(text: str, max_depth: int) -> None:
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > max_depth:
                raise OutboundSecurityError("JSON 嵌套层级超过限制")
        elif char in "]}":
            depth -= 1
            if depth < 0:
                raise OutboundSecurityError("JSON 结构不合法")
    if in_string or depth != 0:
        raise OutboundSecurityError("JSON 结构不合法")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise OutboundSecurityError("JSON 对象包含重复字段")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise OutboundSecurityError(f"JSON 包含非法数值：{value}")


def _validate_tree(value: Any, max_nodes: int) -> None:
    nodes = 0
    stack = [value]
    while stack:
        current = stack.pop()
        nodes += 1
        if nodes > max_nodes:
            raise OutboundSecurityError("JSON 节点数超过限制")
        if isinstance(current, dict):
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
        elif isinstance(current, float) and not math.isfinite(current):
            raise OutboundSecurityError("JSON 包含非法数值")
        elif not isinstance(current, (str, int, float, bool, type(None))):
            raise OutboundSecurityError("JSON 包含不支持的值")


async def read_json_response(
    response: httpx.Response,
    *,
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    max_depth: int = DEFAULT_MAX_JSON_DEPTH,
    max_nodes: int = DEFAULT_MAX_JSON_NODES,
    expected_type: type | tuple[type, ...] = dict,
) -> Any:
    """按解压后的流量计数读取 JSON，读取前后均执行大小与结构限制。"""
    length = _content_length(response)
    if length is not None and length > max_bytes:
        raise OutboundSecurityError("响应 Content-Length 超过大小限制")

    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise OutboundSecurityError("响应超过大小限制")
        chunks.append(chunk)
    try:
        text = b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OutboundSecurityError("JSON 响应不是 UTF-8") from exc
    _check_json_nesting(text, max_depth)
    try:
        data = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except OutboundSecurityError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise OutboundSecurityError("响应不是合法 JSON") from exc
    if not isinstance(data, expected_type):
        raise OutboundSecurityError("JSON 顶层结构不符合预期")
    _validate_tree(data, max_nodes)
    return data


async def read_sse_response(
    response: httpx.Response, *, max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    max_content_chars: int = 500_000,
) -> tuple[str, int]:
    """Decode bounded SSE frames, including split UTF-8, CR/LF/CRLF, and multi-data events.

    Only a clean DONE or finish_reason=stop is accepted. A truncated or filtered
    stream is never promoted to a successful model answer. Anything after [DONE]
    is ignored so a misbehaving trailing event cannot alter or void the answer.
    """
    length = _content_length(response)
    if length is not None and length > max_bytes:
        raise OutboundSecurityError("响应 Content-Length 超过大小限制")
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    pending = ""
    data_lines: list[str] = []
    parts: list[str] = []
    total = 0
    chars = 0
    tokens = 0
    done = False
    finished = False
    first_decode = True

    def consume_event() -> None:
        nonlocal tokens, done, finished, chars
        if done or not data_lines:
            return
        text = "\n".join(data_lines)
        data_lines.clear()
        if text.strip() == "[DONE]":
            done = True
            return
        _check_json_nesting(text, DEFAULT_MAX_JSON_DEPTH)
        try:
            event = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        except (json.JSONDecodeError, RecursionError, ValueError) as exc:
            raise OutboundSecurityError("SSE JSON 不合法") from exc
        _validate_tree(event, DEFAULT_MAX_JSON_NODES)
        if not isinstance(event, dict):
            raise OutboundSecurityError("SSE 事件结构不合法")
        usage = event.get("usage") or {}
        if isinstance(usage, dict) and usage.get("total_tokens") is not None:
            try:
                tokens = int(usage["total_tokens"])
            except (TypeError, ValueError, OverflowError) as exc:
                raise OutboundSecurityError("SSE usage 不合法") from exc
        choices = event.get("choices") or []
        if choices:
            if not isinstance(choices, list) or not isinstance(choices[0], dict):
                raise OutboundSecurityError("SSE choices 不合法")
            choice = choices[0]
            reason = choice.get("finish_reason")
            if reason in ("length", "content_filter"):
                raise SSECompletionError(reason)
            if reason is not None:
                if reason != "stop":
                    raise OutboundSecurityError("SSE 未正常完成")
                finished = True
            delta = choice.get("delta") or {}
            if not isinstance(delta, dict):
                raise OutboundSecurityError("SSE delta 不合法")
            content = delta.get("content")
            if content is not None:
                if not isinstance(content, str):
                    raise OutboundSecurityError("SSE content 不合法")
                chars += len(content)
                if chars > max_content_chars:
                    raise OutboundSecurityError("SSE 内容超过大小限制")
                parts.append(content)

    def feed(text: str) -> None:
        nonlocal pending, done
        if done:
            # Ignore everything after [DONE]; it can never change the answer.
            data_lines.clear()
            return
        pending += text
        # SSE line terminators are CR, LF or CRLF. A trailing CR may still be
        # the first half of a CRLF pair, so hold it until the next byte arrives.
        hold_cr = pending.endswith("\r")
        body = pending[:-1] if hold_cr else pending
        lines = re.split(r"\r\n|\n|\r", body)
        pending = lines.pop()
        for line in lines:
            if not line:
                consume_event()
            elif line.startswith("data:"):
                data_lines.append(line[5:].removeprefix(" "))

    try:
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise OutboundSecurityError("SSE 响应超过大小限制")
            decoded = decoder.decode(chunk)
            if first_decode:
                decoded = decoded.removeprefix("\ufeff")
                first_decode = False
            feed(decoded)
        feed(decoder.decode(b"", final=True))
    except UnicodeDecodeError as exc:
        raise OutboundSecurityError("SSE 不是 UTF-8") from exc
    if done:
        # A clean DONE wins over any trailing partial frame.
        return "".join(parts), tokens
    if pending or data_lines:
        raise SSEInterruptedError("SSE 流中断")
    if not finished:
        raise SSEInterruptedError("SSE 流中断")
    return "".join(parts), tokens


def read_json_response_sync(
    response: httpx.Response,
    *,
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    max_depth: int = DEFAULT_MAX_JSON_DEPTH,
    max_nodes: int = DEFAULT_MAX_JSON_NODES,
    expected_type: type | tuple[type, ...] = dict,
) -> Any:
    """同步调用方使用的受限流式 JSON 读取；应在线程池或同步上下文中调用。"""
    length = _content_length(response)
    if length is not None and length > max_bytes:
        raise OutboundSecurityError("响应 Content-Length 超过大小限制")
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise OutboundSecurityError("响应超过大小限制")
        chunks.append(chunk)
    try:
        text = b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OutboundSecurityError("JSON 响应不是 UTF-8") from exc
    _check_json_nesting(text, max_depth)
    try:
        data = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except OutboundSecurityError:
        raise
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise OutboundSecurityError("响应不是合法 JSON") from exc
    if not isinstance(data, expected_type):
        raise OutboundSecurityError("JSON 顶层结构不符合预期")
    _validate_tree(data, max_nodes)
    return data


def request_json_sync(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    expected_type: type | tuple[type, ...] = dict,
    **kwargs: Any,
) -> tuple[int, Any | None]:
    """执行同步非重定向流式请求；返回状态码，成功时解析受限 JSON。"""
    with client.stream(method, url, **kwargs) as response:
        length = _content_length(response)
        if length is not None and length > max_bytes:
            raise OutboundSecurityError("响应 Content-Length 超过大小限制")
        status_code = response.status_code
        if not 200 <= status_code < 300:
            return status_code, None
        return status_code, read_json_response_sync(
            response, max_bytes=max_bytes, expected_type=expected_type
        )


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    expected_type: type | tuple[type, ...] = dict,
    **kwargs: Any,
) -> tuple[httpx.Response, Any | None]:
    """执行非重定向流式请求；仅对成功响应解析受限 JSON。"""
    async with client.stream(method, url, **kwargs) as response:
        length = _content_length(response)
        if length is not None and length > max_bytes:
            raise OutboundSecurityError("响应 Content-Length 超过大小限制")
        if not 200 <= response.status_code < 300:
            return response, None
        data = await read_json_response(
            response, max_bytes=max_bytes, expected_type=expected_type
        )
        return response, data
