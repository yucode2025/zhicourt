"""Bounded, origin-bound OpenAI-compatible LLM gateway."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator

import anyio
import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.core.url_guard import UnsafeURLError, normalize_provider_url, provider_origin
from app.services import api_usage
from app.services.outbound_security import (
    OutboundSecurityError,
    SSECompletionError,
    SSEInterruptedError,
    read_sse_response,
    request_json,
    validate_outbound_url,
)

logger = get_logger(__name__)


class LLMError(Exception):
    """Safe public error; never includes upstream bodies, URLs or credentials."""

    def __init__(self, code: str = "provider_error", *, retryable: bool = False,
                 disposition: str = "deterministic") -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.disposition = disposition


class LLMUnavailable(LLMError):
    def __init__(self, code: str = "unconfigured", *, retryable: bool = False,
                 disposition: str = "deterministic") -> None:
        super().__init__(code, retryable=retryable, disposition=disposition)


_LLM_BAD_GATEWAY_CODES = {
    "http_rejected", "incomplete_response", "invalid_json", "invalid_response",
    "stream_interrupted",
}
_LLM_PUBLIC_MESSAGES = {
    "authentication": "模型服务认证失败，请联系管理员检查配置。",
    "unconfigured": "模型服务尚未配置，请联系管理员。",
    "outbound_security": "模型服务地址不符合安全要求，请联系管理员检查配置。",
    "rate_limited": "模型服务请求过于频繁，请稍后重试。",
    "incomplete_response": "模型服务返回内容不完整，请稍后重试。",
    "invalid_json": "模型服务返回格式异常，请稍后重试。",
    "invalid_response": "模型服务返回异常，请稍后重试。",
}




def llm_error_http_status(exc: LLMError) -> int:
    if exc.code == "rate_limited":
        return 429
    if exc.code in _LLM_BAD_GATEWAY_CODES:
        return 502
    return 503



def llm_error_public_message(exc: LLMError) -> str:
    if exc.code in _LLM_PUBLIC_MESSAGES:
        return _LLM_PUBLIC_MESSAGES[exc.code]
    if exc.disposition == "transient":
        return "模型服务暂时不可用，请稍后重试。"
    return "模型服务不可用，请联系管理员检查配置。"



def llm_error_is_retryable(exc: LLMError) -> bool:
    return exc.retryable or exc.disposition == "transient"



def llm_error_code_is_retryable(code: str) -> bool:
    return bool(code) and code not in {
        "authentication", "unconfigured", "outbound_security", "case_short_circuit",
    }

@dataclass
class LLMResult:
    content: str
    data: Any = None
    token_usage: int = 0
    request_id: str = ""
    raw_error: str = ""


@dataclass
class CaseLLMState:
    """Per-case short circuit; transient failures never poison a case."""
    blocked: bool = False
    code: str = ""
    fail_on_error: bool = False


_case_state: ContextVar[CaseLLMState | None] = ContextVar("llm_case_state", default=None)


@contextmanager
def llm_case_scope(*, fail_on_error: bool = False) -> Iterator[CaseLLMState]:
    """Wrap a case execution with this API; nested/concurrent cases stay isolated."""
    state = CaseLLMState(fail_on_error=fail_on_error)
    token = _case_state.set(state)
    try:
        yield state
    finally:
        _case_state.reset(token)


def llm_case_short_circuited() -> bool:
    state = _case_state.get()
    return state is not None and state.blocked



def llm_case_requires_success() -> bool:
    state = _case_state.get()
    return state is not None and state.fail_on_error

# Only configuration-level failures block the remaining LLM calls of a case.
# A single malformed response, truncation, proxy hiccup or 4xx body must degrade
# the current agent only, never silently downgrade the rest of the trial.
_CASE_BLOCKING_CODES = {"unconfigured", "authentication", "case_short_circuit"}


def _should_block_case(exc: LLMError) -> bool:
    return exc.code in _CASE_BLOCKING_CODES or exc.disposition == "security"



def _extract_json(text: str) -> Any:
    """Extract fenced or embedded JSON without using provider text in errors."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("{")
    if start == -1:
        start = text.find("[")
    if start == -1:
        raise ValueError("no json object found")
    open_char = text[start]
    close_char = "}" if open_char == "{" else "]"
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise ValueError("unbalanced json")


class LLMGateway:
    CAPABILITY_TTL = 600.0
    CIRCUIT_COOLDOWN = 30.0
    CIRCUIT_THRESHOLD = 3
    MAX_RETRY_TOKENS = 16_000

    def __init__(self) -> None:
        self.base_url = settings.llm_base_url
        self.api_key = settings.llm_api_key
        self.model = settings.llm_model
        self.timeout = settings.llm_timeout
        self.max_response_bytes = 2 * 1024 * 1024
        self.record_usage = True
        self._runtime_override = False
        self.generation = 0
        self._capabilities: dict[tuple[int, str, str, str], tuple[float, bool, bool]] = {}
        self._circuit_lock = threading.Lock()
        self._circuit = "CLOSED"
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_busy = False

    @property
    def enabled(self) -> bool:
        if self._runtime_override:
            return bool(self.base_url and self.api_key and self.model)
        return settings.llm_enabled

    def configure(self, base_url: str, api_key: str, model: str,
                  *, fallback_to_env: bool = True) -> None:
        if not (base_url and api_key and model) and fallback_to_env:
            base_url, api_key, model = settings.llm_base_url, settings.llm_api_key, settings.llm_model
            override = False
        else:
            override = True
        normalized = normalize_provider_url(base_url) if base_url else ""
        if (normalized, api_key, model, override) == (
            self.base_url, self.api_key, self.model, self._runtime_override
        ):
            return
        self.base_url, self.api_key, self.model = normalized, api_key, model
        self._runtime_override = override
        self.generation += 1
        self._capabilities.clear()
        self._circuit = "CLOSED"
        self._failures = 0
        self._half_open_busy = False
        logger.info("LLM gateway configuration changed (generation=%d)", self.generation)

    def status_snapshot(self) -> dict[str, Any]:
        """No origin, model, key, fingerprint, or upstream error text is exposed."""
        state = self._circuit
        if state == "OPEN" and time.monotonic() - self._opened_at >= self.CIRCUIT_COOLDOWN:
            state = "HALF_OPEN"
        return {"enabled": self.enabled, "generation": self.generation,
                "circuit": state, "consecutive_failures": self._failures}

    def _capability_key(self, base_url: str, model: str, api_key: str,
                        generation: int) -> tuple[int, str, str, str]:
        return (generation, provider_origin(base_url), model,
                hashlib.sha256(api_key.encode()).hexdigest())

    def _enter_circuit(self) -> None:
        with self._circuit_lock:
            if self._circuit == "OPEN":
                if time.monotonic() - self._opened_at < self.CIRCUIT_COOLDOWN:
                    raise LLMUnavailable("circuit_open", retryable=True, disposition="transient")
                self._circuit = "HALF_OPEN"
            if self._circuit == "HALF_OPEN":
                if self._half_open_busy:
                    raise LLMUnavailable("circuit_open", retryable=True, disposition="transient")
                self._half_open_busy = True

    def _finish_circuit(self, error: LLMError | None) -> None:
        with self._circuit_lock:
            if error is None or not error.retryable:
                self._failures = 0
                self._circuit = "CLOSED"
            else:
                self._failures += 1
                if self._circuit == "HALF_OPEN" or self._failures >= self.CIRCUIT_THRESHOLD:
                    self._circuit = "OPEN"
                    self._opened_at = time.monotonic()
            self._half_open_busy = False

    async def chat(self, system: str, user: str, *, json_mode: bool = False,
                   max_tokens: int = 3000, temperature: float = 0.3) -> LLMResult:
        case = _case_state.get()
        if case is not None and case.blocked:
            raise LLMUnavailable("case_short_circuit", disposition="deterministic")
        if not self.enabled:
            if case is not None:
                case.blocked, case.code = True, "unconfigured"
            raise LLMUnavailable()
        try:
            self._enter_circuit()
        except LLMError:
            if self.record_usage:
                api_usage.record("llm", "/chat/completions", failure=True)
            raise
        base_url, api_key, model, generation = (
            self.base_url, self.api_key, self.model, self.generation
        )
        request_id = uuid.uuid4().hex[:12]
        deadline = time.monotonic() + max(0.0, self.timeout)
        error: LLMError | None = None
        try:
            key = self._capability_key(base_url, model, api_key, generation)
            cached = self._capabilities.get(key)
            stream, structured = (cached[1], cached[2]) if cached and cached[0] > time.monotonic() else (True, True)
            transient_attempts = 0
            attempt_max_tokens = max_tokens
            failure: LLMError
            while True:
                if generation != self.generation:
                    raise LLMUnavailable("configuration_changed", disposition="transient")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LLMUnavailable("deadline_exceeded", retryable=True, disposition="transient")
                payload: dict[str, Any] = {
                    "model": model,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                    "max_tokens": attempt_max_tokens, "temperature": temperature, "stream": stream,
                }
                if json_mode and structured:
                    payload["response_format"] = {"type": "json_object"}
                try:
                    # Revalidate DNS and exact origin on EVERY request (including feature
                    # negotiation). A wrong target is a configuration-level failure and may
                    # short-circuit the case; response-body issues below may not.
                    try:
                        url = validate_outbound_url(
                            f"{base_url}/chat/completions", allowed_origins=[base_url]
                        )
                    except (UnsafeURLError, OutboundSecurityError):
                        raise LLMUnavailable("outbound_security", disposition="security") from None
                    with anyio.fail_after(remaining):
                        async with httpx.AsyncClient(timeout=remaining, follow_redirects=False) as client:
                            if stream:
                                async with client.stream("POST", url, json=payload,
                                                         headers={"Authorization": f"Bearer {api_key}"}) as resp:
                                    status = resp.status_code
                                    if 200 <= status < 300:
                                        if "text/event-stream" in resp.headers.get("content-type", "").lower():
                                            content, tokens = await read_sse_response(
                                                resp, max_bytes=self.max_response_bytes
                                            )
                                        else:
                                            # Some compatible providers ignore stream and return JSON.
                                            from app.services.outbound_security import read_json_response
                                            body = await read_json_response(
                                                resp, max_bytes=self.max_response_bytes
                                            )
                                            content, tokens = self._parse_body(body)
                            else:
                                resp, body = await request_json(
                                    client, "POST", url, max_bytes=self.max_response_bytes, json=payload,
                                    headers={"Authorization": f"Bearer {api_key}"}
                                )
                                status = resp.status_code
                                if 200 <= status < 300:
                                    content, tokens = self._parse_body(body)
                    if status in (400, 422):
                        if self.record_usage:
                            api_usage.record("llm", "/chat/completions", failure=True)
                        if stream:
                            stream = False
                            continue
                        if json_mode and structured:
                            structured = False
                            continue
                    if not 200 <= status < 300:
                        raise self._http_error(status)
                    result = LLMResult(content=content, token_usage=tokens, request_id=request_id)
                    if json_mode:
                        try:
                            result.data = _extract_json(content)
                        except (ValueError, TypeError, RecursionError):
                            raise LLMError("invalid_json", retryable=True, disposition="transient") from None
                    if generation == self.generation:
                        self._capabilities[key] = (time.monotonic() + self.CAPABILITY_TTL, stream, structured)
                    if self.record_usage:
                        api_usage.record("llm", "/chat/completions")
                    return result
                except SSEInterruptedError:
                    failure = LLMUnavailable("stream_interrupted", retryable=True, disposition="transient")
                except SSECompletionError as exc:
                    # A length-limited answer can succeed with a larger output budget.
                    # Content filtering is deterministic for the same prompt, so let the
                    # failover gateway try another endpoint without replaying it here.
                    failure = LLMError(
                        "incomplete_response", retryable=exc.reason == "length",
                        disposition="transient",
                    )
                except OutboundSecurityError:
                    # Response-body safety violation (bad event, oversize, bad encoding):
                    # a one-off transport problem, not proof the case configuration is broken.
                    raise LLMError("invalid_response", retryable=True, disposition="transient") from None
                except (TimeoutError, httpx.TimeoutException):
                    failure = LLMUnavailable("timeout", retryable=True, disposition="transient")
                except httpx.RequestError:
                    failure = LLMUnavailable("network", retryable=True, disposition="transient")
                except (KeyError, IndexError, TypeError, ValueError, OverflowError) as exc:
                    raise LLMError("invalid_response", retryable=True, disposition="transient") from None
                except LLMError as exc:
                    failure = exc
                if self.record_usage:
                    api_usage.record("llm", "/chat/completions", failure=True)
                if not failure.retryable:
                    raise failure
                transient_attempts += 1
                remaining = deadline - time.monotonic()
                if transient_attempts >= 3 or remaining <= 0:
                    raise failure
                if failure.code == "incomplete_response":
                    attempt_max_tokens = min(
                        max(attempt_max_tokens + 1, attempt_max_tokens * 2),
                        self.MAX_RETRY_TOKENS,
                    )
                delay = min(0.25 * 2 ** (transient_attempts - 1), remaining)
                await asyncio.sleep(delay)
        except LLMError as exc:
            error = exc
            if case is not None and _should_block_case(exc):
                case.blocked, case.code = True, exc.code
            raise
        finally:
            # An in-flight request from an old configuration cannot change the
            # new generation's circuit, including HALF_OPEN admission state.
            # An unexpected exception (cancellation, interpreter-level error)
            # must count as a transient failure — never clear the circuit.
            if generation == self.generation:
                if error is not None:
                    self._finish_circuit(error)
                elif sys.exc_info()[0] is not None:
                    self._finish_circuit(LLMError("internal_error", retryable=True, disposition="transient"))
                else:
                    self._finish_circuit(None)

    @staticmethod
    def _parse_body(body: Any) -> tuple[str, int]:
        content = body["choices"][0]["message"]["content"]
        finish = body["choices"][0].get("finish_reason")
        if finish in ("length", "content_filter"):
            raise LLMError(
                "incomplete_response", retryable=finish == "length",
                disposition="transient",
            )
        if not isinstance(content, str):
            raise LLMError("invalid_response", retryable=True, disposition="transient")
        usage = body.get("usage") or {}
        try:
            tokens = int(usage.get("total_tokens", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            tokens = 0
        return content, tokens

    @staticmethod
    def _http_error(status: int) -> LLMError:
        if status in (401, 403):
            return LLMUnavailable("authentication")
        if status == 408:
            return LLMUnavailable("timeout", retryable=True, disposition="transient")
        if status == 429:
            return LLMUnavailable("rate_limited", retryable=True, disposition="transient")
        if 500 <= status <= 599:
            return LLMUnavailable("upstream_unavailable", retryable=True, disposition="transient")
        # Other 4xx/3xx are per-request failures; they degrade the current agent
        # but are never proof that the case configuration is broken.
        return LLMError("http_rejected", disposition="transient")


class LLMFailoverGateway(LLMGateway):
    """Primary gateway plus ordered, isolated backup endpoints.

    Each endpoint keeps its own capability cache and circuit breaker.  A case is
    short-circuited only when every configured endpoint has a deterministic
    configuration/security failure.
    """

    MAX_ENDPOINTS = 3

    def __init__(self) -> None:
        super().__init__()
        self._fallbacks: list[LLMGateway] = []
        self._endpoint_signature: tuple[tuple[str, str, str], ...] = (
            ((self.base_url, self.api_key, self.model),)
            if self.base_url and self.api_key and self.model else ()
        )

    @property
    def endpoint_count(self) -> int:
        return len(self._endpoint_signature)

    def configure(self, base_url: str, api_key: str, model: str,
                  *, fallback_to_env: bool = True) -> None:
        self.configure_endpoints(
            [{"base_url": base_url, "api_key": api_key, "model": model}],
            fallback_to_env=fallback_to_env,
        )

    def configure_endpoints(self, endpoints: list[dict[str, str]],
                            *, fallback_to_env: bool = True) -> None:
        prepared: list[tuple[str, str, str]] = []
        for raw in endpoints[: self.MAX_ENDPOINTS]:
            base_url = (raw.get("base_url") or "").strip()
            api_key = (raw.get("api_key") or "").strip()
            model = (raw.get("model") or "").strip()
            if not (base_url and api_key and model):
                continue
            endpoint = (normalize_provider_url(base_url), api_key, model)
            if endpoint not in prepared:
                prepared.append(endpoint)
        override = True
        if not prepared and fallback_to_env and settings.llm_enabled:
            prepared = [(normalize_provider_url(settings.llm_base_url),
                         settings.llm_api_key, settings.llm_model)]
            override = False
        signature = tuple(prepared)
        if signature == self._endpoint_signature and override == self._runtime_override:
            return

        primary = signature[0] if signature else ("", "", "")
        self.base_url, self.api_key, self.model = primary
        self._runtime_override = override
        self._endpoint_signature = signature
        self._fallbacks = []
        for base_url, api_key, model in signature[1:]:
            child = LLMGateway()
            child.timeout = self.timeout
            child.max_response_bytes = self.max_response_bytes
            child.record_usage = self.record_usage
            child.configure(base_url, api_key, model, fallback_to_env=False)
            self._fallbacks.append(child)

        self.generation += 1
        self._capabilities.clear()
        self._circuit = "CLOSED"
        self._failures = 0
        self._half_open_busy = False
        logger.info(
            "LLM gateway endpoint pool changed (generation=%d, endpoints=%d)",
            self.generation, len(signature),
        )

    @property
    def enabled(self) -> bool:
        return bool(self._endpoint_signature)

    def status_snapshot(self) -> dict[str, Any]:
        snapshot = super().status_snapshot()
        snapshot["endpoint_count"] = self.endpoint_count
        if not self.endpoint_count:
            snapshot["endpoints"] = []
            return snapshot
        snapshot["endpoints"] = [{
            "index": 1,
            "circuit": snapshot["circuit"],
            "consecutive_failures": snapshot["consecutive_failures"],
        }]
        for index, item in enumerate(self._fallbacks, start=2):
            item_snapshot = item.status_snapshot()
            snapshot["endpoints"].append({
                "index": index,
                "circuit": item_snapshot["circuit"],
                "consecutive_failures": item_snapshot["consecutive_failures"],
            })
        return snapshot

    async def chat(self, system: str, user: str, *, json_mode: bool = False,
                   max_tokens: int = 3000, temperature: float = 0.3) -> LLMResult:
        case = _case_state.get()
        if case is not None and case.blocked:
            raise LLMUnavailable("case_short_circuit", disposition="deterministic")
        if not self.enabled:
            if case is not None:
                case.blocked, case.code = True, "unconfigured"
            raise LLMUnavailable()

        failures: list[LLMError] = []
        for index, endpoint in enumerate([self, *self._fallbacks]):
            # A failed endpoint must not poison the case before backups are tried.
            token = _case_state.set(None)
            try:
                if endpoint is self:
                    result = await super().chat(
                        system, user, json_mode=json_mode,
                        max_tokens=max_tokens, temperature=temperature,
                    )
                else:
                    result = await endpoint.chat(
                        system, user, json_mode=json_mode,
                        max_tokens=max_tokens, temperature=temperature,
                    )
                if index:
                    logger.warning("LLM request succeeded through backup endpoint %d", index)
                return result
            except LLMError as exc:
                failures.append(exc)
                logger.warning("LLM endpoint %d failed: %s", index + 1, exc.code)
            finally:
                _case_state.reset(token)

        failure = failures[-1] if failures else LLMUnavailable()
        if case is not None and failures and all(_should_block_case(item) for item in failures):
            case.blocked, case.code = True, failure.code
        raise failure


gateway = LLMFailoverGateway()
