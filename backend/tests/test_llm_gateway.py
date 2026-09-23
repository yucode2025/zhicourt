"""LLM Gateway 单元测试（不发起真实网络请求）。"""
from __future__ import annotations

import importlib

import httpx
import pytest

from app.services.llm.gateway import (
    LLMError, LLMFailoverGateway, LLMGateway, LLMUnavailable, _extract_json,
    gateway, llm_case_scope,
)
from app.agents.base import llm_json
from app.services.outbound_security import OutboundSecurityError, read_sse_response

gateway_module = importlib.import_module("app.services.llm.gateway")


def test_extract_json_plain():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_with_fences():
    text = '```json\n{"a": [1, 2], "b": "x"}\n```'
    assert _extract_json(text) == {"a": [1, 2], "b": "x"}


def test_extract_json_with_prefix_text():
    text = '好的，以下是结果：\n{"a": {"b": 2}}\n希望有帮助'
    assert _extract_json(text) == {"a": {"b": 2}}


def test_extract_json_nested_strings():
    text = '{"s": "he said \\"ok\\" {fine}"}'
    assert _extract_json(text)["s"].startswith("he said")


def test_extract_json_invalid_raises():
    with pytest.raises(ValueError):
        _extract_json("no json here")


def test_gateway_disabled_raises():
    # 测试环境未配置 LLM
    with pytest.raises(LLMUnavailable):
        import asyncio

        asyncio.run(gateway.chat("sys", "user"))


@pytest.mark.asyncio
async def test_gateway_validates_before_outbound(monkeypatch):
    g = LLMGateway()
    g.configure("http://127.0.0.1:11434/v1", "super-secret", "m")
    opened = False

    class ShouldNotOpen:
        def __init__(self, *args, **kwargs):
            nonlocal opened
            opened = True

    monkeypatch.setattr(gateway_module.httpx, "AsyncClient", ShouldNotOpen)
    with pytest.raises(LLMUnavailable) as exc_info:
        await g.chat("sys", "user")
    assert opened is False
    assert "super-secret" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_gateway_disables_redirects(monkeypatch):
    g = LLMGateway()
    g.configure("https://93.184.216.34/v1", "super-secret", "m")
    seen: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        headers = {"Content-Length": "69"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def aiter_bytes(self):
            yield b'{"choices":[{"message":{"content":"ok"}}],"usage":{}}'

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

    monkeypatch.setattr(gateway_module.httpx, "AsyncClient", FakeClient)
    result = await g.chat("sys", "user")
    assert result.content == "ok"
    assert seen["follow_redirects"] is False


def _mock_provider(monkeypatch, statuses, *, stream=False):
    """Fake network; retain each request's headers and JSON for origin checks."""
    seen = []
    response_bytes = b'{"choices":[{"message":{"content":"{\\"ok\\":true}"},"finish_reason":"stop"}],"usage":{"total_tokens":7}}'

    class FakeResponse:
        def __init__(self, status):
            self.status_code = status
            self.headers = {"content-type": "application/json"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def aiter_bytes(self):
            yield response_bytes

    class FakeClient:
        def __init__(self, *args, **kwargs):
            assert kwargs["follow_redirects"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def stream(self, method, url, **kwargs):
            seen.append((url, kwargs))
            return FakeResponse(statuses[min(len(seen) - 1, len(statuses) - 1)])

    monkeypatch.setattr(gateway_module.httpx, "AsyncClient", FakeClient)
    return seen


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code,retryable,count", [
    (401, "authentication", False, 1), (403, "authentication", False, 1),
    (404, "http_rejected", False, 1), (408, "timeout", True, 3),
    (429, "rate_limited", True, 3),
    (503, "upstream_unavailable", True, 3),
])
async def test_http_error_policy(monkeypatch, status, code, retryable, count):
    g = LLMGateway()
    g.configure("https://93.184.216.34/v1", "secret", "model")
    seen = _mock_provider(monkeypatch, [status])
    with pytest.raises(LLMError) as caught:
        await g.chat("sys", "usr")
    assert (caught.value.code, caught.value.retryable) == (code, retryable)
    # 401/403 是配置级失败可短路案件；其余 4xx/3xx 是单次请求失败，
    # 只能降级当前 Agent，绝不能毒化整案。
    if status in (401, 403):
        assert caught.value.disposition == "deterministic"
    else:
        assert caught.value.disposition == "transient"
    assert len(seen) == count
    assert all(item[1]["headers"]["Authorization"] == "Bearer secret" for item in seen)


@pytest.mark.asyncio
async def test_deadline_includes_backoff(monkeypatch):
    g = LLMGateway()
    g.configure("https://93.184.216.34/v1", "key", "model")
    g.timeout = 0.1
    seen = _mock_provider(monkeypatch, [429])
    with pytest.raises(LLMError):
        await g.chat("s", "u")
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_negotiation_cache_and_generation(monkeypatch):
    g = LLMGateway()
    g.configure("https://93.184.216.34/v1", "old-secret", "m")
    seen = _mock_provider(monkeypatch, [400, 200, 200, 400, 200])
    calls = []
    original = gateway_module.validate_outbound_url

    def validated(url, *, allowed_origins):
        calls.append(url)
        return original(url, allowed_origins=allowed_origins)

    monkeypatch.setattr(gateway_module, "validate_outbound_url", validated)
    assert (await g.chat("s", "u", json_mode=True)).data == {"ok": True}
    assert len(seen) == 2
    assert seen[0][1]["json"]["stream"] is True
    assert seen[1][1]["json"]["stream"] is False
    await g.chat("s", "u", json_mode=True)
    assert len(seen) == 3 and len(calls) == 3
    before = g.generation
    g.configure("https://93.184.216.34/v1", "new-secret", "m")
    assert g.generation == before + 1 and not g._capabilities
    await g.chat("s", "u")
    assert seen[3][1]["headers"]["Authorization"] == "Bearer new-secret"


@pytest.mark.asyncio
async def test_case_scope_blocks_deterministic_but_not_transient(monkeypatch):
    g = LLMGateway()
    g.configure("https://93.184.216.34/v1", "secret", "m")
    seen = _mock_provider(monkeypatch, [401, 200])
    with llm_case_scope() as state:
        with pytest.raises(LLMError):
            await g.chat("s", "u")
        assert state.blocked and state.code == "authentication"
        with pytest.raises(LLMUnavailable, match="case_short_circuit"):
            await g.chat("s", "u")
        assert len(seen) == 1
    assert (await g.chat("s", "u")).content
    assert len(seen) == 2

    other = LLMGateway()
    other.configure("https://93.184.216.34/v1", "secret", "m")
    responses = _mock_provider(monkeypatch, [429, 429, 429, 200])
    with llm_case_scope() as state:
        with pytest.raises(LLMError) as failure:
            await other.chat("s", "u")
        assert failure.value.disposition == "transient" and not state.blocked
        await other.chat("s", "u")
    assert len(responses) == 4


@pytest.mark.asyncio
async def test_per_request_failures_never_poison_case(monkeypatch):
    """404 与 invalid_json 只降级当前 Agent；本案后续 Agent 仍可重试 LLM。"""
    g = LLMGateway()
    g.configure("https://93.184.216.34/v1", "secret", "m")
    seen = _mock_provider(monkeypatch, [404, 200])
    with llm_case_scope() as state:
        with pytest.raises(LLMError) as http_failure:
            await g.chat("s", "u")
        assert http_failure.value.code == "http_rejected"
        assert not state.blocked
        assert (await g.chat("s", "u")).content
    assert len(seen) == 2

    seen = _mock_provider(monkeypatch, [200, 200])
    monkeypatch.setattr(gateway_module, "_extract_json", lambda _content: (_ for _ in ()).throw(ValueError("bad")))
    with llm_case_scope() as state:
        with pytest.raises(LLMError) as json_failure:
            await g.chat("s", "u", json_mode=True)
        assert json_failure.value.code == "invalid_json"
        assert json_failure.value.retryable
        assert not state.blocked
        assert (await g.chat("s", "u")).content
    assert len(seen) >= 3


@pytest.mark.asyncio
async def test_unexpected_exception_counts_as_circuit_failure(monkeypatch):
    """非 LLMError 异常传播时，熔断必须记一次失败，绝不能清零。"""
    g = LLMGateway()
    g.configure("https://93.184.216.34/v1", "secret", "m")
    g.CIRCUIT_THRESHOLD = 2
    _mock_provider(monkeypatch, [503])

    class CancelledClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def stream(self, *args, **kwargs):
            raise KeyboardInterrupt

    monkeypatch.setattr(gateway_module.httpx, "AsyncClient", CancelledClient)
    with pytest.raises(KeyboardInterrupt):
        await g.chat("s", "u")
    assert g.status_snapshot()["consecutive_failures"] == 1
    assert g.status_snapshot()["circuit"] == "CLOSED"


@pytest.mark.asyncio
async def test_circuit_open_half_open_and_snapshot(monkeypatch):
    g = LLMGateway()
    g.configure("https://93.184.216.34/v1", "secret-sentinel", "model-sentinel")
    g.CIRCUIT_THRESHOLD = 1
    seen = _mock_provider(monkeypatch, [503, 503, 503, 200])
    with pytest.raises(LLMError):
        await g.chat("s", "u")
    assert g.status_snapshot()["circuit"] == "OPEN"
    assert "secret-sentinel" not in str(g.status_snapshot())
    assert "model-sentinel" not in str(g.status_snapshot())
    with pytest.raises(LLMUnavailable, match="circuit_open"):
        await g.chat("s", "u")
    assert len(seen) == 3
    g._opened_at -= g.CIRCUIT_COOLDOWN + 1
    assert g.status_snapshot()["circuit"] == "HALF_OPEN"
    assert (await g.chat("s", "u")).content
    assert g.status_snapshot()["circuit"] == "CLOSED"


@pytest.mark.asyncio
async def test_sse_utf8_crlf_multidata_usage_and_completion():
    payload = (
        'data: {"choices":[{"delta":{"content":"你"}}]}\r\n\r\n'
        'data: {"choices":[{"delta":{"content":"好"},\r\n'
        'data: "finish_reason":"stop"}],"usage":{"total_tokens":9}}\r\n\r\n'
        'data: [DONE]\r\n\r\n'
    ).encode()

    class Response:
        headers = {}

        async def aiter_bytes(self):
            for byte in payload:
                yield bytes([byte])

    assert await read_sse_response(Response()) == ("你好", 9)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n',
    b'data: {"choices":[{"delta":{"content":"x"},"finish_reason":"length"}]}\n\n',
    b'data: {"choices":[{"delta":{"content":"x"},"finish_reason":"content_filter"}]}\n\n',
    b'data: {"choices":[{"delta":{"content":"x"}}]}\n',
    b'data: \xff\n\n',
])
async def test_sse_rejects_incomplete_or_invalid(payload):
    response = httpx.Response(200, content=payload)
    with pytest.raises(OutboundSecurityError):
        await read_sse_response(response)


@pytest.mark.asyncio
async def test_sse_enforces_decompressed_size():
    response = httpx.Response(200, content=b'data: [DONE]\n\n')
    with pytest.raises(OutboundSecurityError):
        await read_sse_response(response, max_bytes=3)
    response = httpx.Response(200, content=b'data: [DONE]\n\n')
    with pytest.raises(OutboundSecurityError):
        await read_sse_response(response, max_content_chars=0, max_bytes=3)


@pytest.mark.asyncio
async def test_sse_content_character_limit():
    response = httpx.Response(
        200, content=b'data: {"choices":[{"delta":{"content":"too long"},"finish_reason":"stop"}]}\n\n'
    )
    with pytest.raises(OutboundSecurityError):
        await read_sse_response(response, max_content_chars=2)


@pytest.mark.asyncio
async def test_concurrent_case_scopes_are_isolated(monkeypatch):
    import asyncio

    g = LLMGateway()
    g.configure("https://93.184.216.34/v1", "secret", "m")
    seen = _mock_provider(monkeypatch, [401, 200])
    first_done = asyncio.Event()

    async def first():
        with llm_case_scope() as state:
            with pytest.raises(LLMError):
                await g.chat("s", "u")
            first_done.set()
            await asyncio.sleep(0)
            assert state.blocked

    async def second():
        with llm_case_scope() as state:
            await first_done.wait()
            result = await g.chat("s", "u")
            assert result.content and not state.blocked

    await asyncio.gather(first(), second())
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_security_failure_does_not_retry_or_open_circuit(monkeypatch):
    g = LLMGateway()
    g.configure("https://93.184.216.34/v1", "secret", "m")
    opened = _mock_provider(monkeypatch, [200])

    def reject(*args, **kwargs):
        raise OutboundSecurityError("secret should never be forwarded")

    monkeypatch.setattr(gateway_module, "validate_outbound_url", reject)
    with llm_case_scope() as state:
        with pytest.raises(LLMUnavailable) as caught:
            await g.chat("s", "u")
        assert (caught.value.code, caught.value.retryable, caught.value.disposition) == (
            "outbound_security", False, "security"
        )
        assert state.blocked
    assert not opened and g.status_snapshot()["circuit"] == "CLOSED"


@pytest.mark.asyncio
async def test_agent_wrapper_short_circuits_case(monkeypatch):
    import app.agents.base as base

    g = LLMGateway()
    g.configure("https://93.184.216.34/v1", "secret", "m")
    seen = _mock_provider(monkeypatch, [401, 200])
    monkeypatch.setattr(base, "gateway", g)
    with llm_case_scope() as state:
        assert await llm_json("s", "u") is None
        assert state.blocked
        assert await llm_json("s", "u") is None
        assert len(seen) == 1
    assert await llm_json("s", "u") == {"ok": True}


@pytest.mark.asyncio
async def test_capability_ttl_expiration(monkeypatch):
    g = LLMGateway()
    g.configure("https://93.184.216.34/v1", "key", "m")
    seen = _mock_provider(monkeypatch, [400, 200, 200, 400, 200])
    await g.chat("s", "u")
    await g.chat("s", "u")
    assert [x[1]["json"]["stream"] for x in seen] == [True, False, False]
    key = next(iter(g._capabilities))
    _expires, stream, structured = g._capabilities[key]
    g._capabilities[key] = (0.0, stream, structured)
    await g.chat("s", "u")
    assert [x[1]["json"]["stream"] for x in seen[3:]] == [True, False]


@pytest.mark.asyncio
async def test_network_timeout_and_stream_interrupt_are_transient(monkeypatch):
    g = LLMGateway()
    g.configure("https://93.184.216.34/v1", "key", "m")
    seen = []

    class BrokenClient:
        def __init__(self, *args, **kwargs):
            seen.append(kwargs["follow_redirects"])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def stream(self, method, url, **kwargs):
            raise httpx.ConnectError("sensitive URL", request=httpx.Request("POST", url))

    monkeypatch.setattr(gateway_module.httpx, "AsyncClient", BrokenClient)
    with pytest.raises(LLMError) as caught:
        await g.chat("s", "u")
    assert caught.value.code == "network" and caught.value.retryable
    assert len(seen) == 3 and not any(seen)
    class Interrupted:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def aiter_bytes(self):
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'

    BrokenClient.stream = lambda self, *args, **kwargs: Interrupted()
    with pytest.raises(LLMError) as caught:
        await g.chat("s", "u")
    assert caught.value.code == "stream_interrupted" and caught.value.disposition == "transient"


@pytest.mark.asyncio
async def test_length_completion_retries_with_larger_token_budget(monkeypatch):
    g = LLMGateway()
    g.configure("https://93.184.216.34/v1", "key", "model")
    payloads = []
    responses = [
        b'{"choices":[{"message":{"content":"partial"},"finish_reason":"length"}]}',
        b'{"choices":[{"message":{"content":"{\\"ok\\":true}"},"finish_reason":"stop"}]}',
    ]

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def __init__(self, content):
            self.content = content

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def aiter_bytes(self):
            yield self.content

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def stream(self, _method, _url, **kwargs):
            payloads.append(kwargs["json"])
            return FakeResponse(responses[len(payloads) - 1])

    monkeypatch.setattr(gateway_module.httpx, "AsyncClient", FakeClient)
    result = await g.chat("s", "u", json_mode=True, max_tokens=100)

    assert result.data == {"ok": True}
    assert [payload["max_tokens"] for payload in payloads] == [100, 200]


@pytest.mark.asyncio
async def test_content_filter_does_not_replay_same_endpoint(monkeypatch):
    g = LLMGateway()
    g.configure("https://93.184.216.34/v1", "key", "model")
    seen = []
    response = b'{"choices":[{"message":{"content":""},"finish_reason":"content_filter"}]}'

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def aiter_bytes(self):
            yield response

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def stream(self, _method, _url, **kwargs):
            seen.append(kwargs["json"])
            return FakeResponse()

    monkeypatch.setattr(gateway_module.httpx, "AsyncClient", FakeClient)
    with pytest.raises(LLMError) as caught:
        await g.chat("s", "u", max_tokens=100)

    assert caught.value.code == "incomplete_response"
    assert caught.value.retryable is False
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_strict_case_scope_propagates_exhausted_llm_error(monkeypatch):
    import app.agents.base as base

    class FailingGateway:
        enabled = True

        async def chat(self, *_args, **_kwargs):
            raise LLMUnavailable("circuit_open", retryable=True, disposition="transient")

    monkeypatch.setattr(base, "gateway", FailingGateway())
    with llm_case_scope():
        assert await llm_json("s", "u") is None
    with llm_case_scope(fail_on_error=True):
        with pytest.raises(LLMUnavailable, match="circuit_open"):
            await llm_json("s", "u")




@pytest.mark.asyncio
async def test_failover_uses_backup_after_incomplete_response(monkeypatch):
    from app.services.llm.gateway import LLMResult

    g = LLMFailoverGateway()
    g.configure_endpoints([
        {"base_url": "https://93.184.216.34/v1", "api_key": "k1", "model": "m1"},
        {"base_url": "https://93.184.216.35/v1", "api_key": "k2", "model": "m2"},
    ], fallback_to_env=False)
    calls = []

    async def chat(self, *_args, **_kwargs):
        calls.append(self)
        if self is g:
            raise LLMError("incomplete_response", disposition="transient")
        return LLMResult(content="ok")

    monkeypatch.setattr(LLMGateway, "chat", chat)
    assert (await g.chat("s", "u")).content == "ok"
    assert len(calls) == 2




@pytest.mark.asyncio
async def test_failover_uses_backup_after_primary_503(monkeypatch):
    g = LLMFailoverGateway()
    g.configure_endpoints([
        {"base_url": "https://93.184.216.34/v1", "api_key": "primary-key", "model": "primary-model"},
        {"base_url": "https://93.184.216.35/v1", "api_key": "backup-key", "model": "backup-model"},
    ], fallback_to_env=False)
    seen = _mock_provider(monkeypatch, [503, 503, 503, 200])

    result = await g.chat("s", "u")

    assert result.content
    assert len(seen) == 4
    assert [call[1]["headers"]["Authorization"] for call in seen] == [
        "Bearer primary-key", "Bearer primary-key", "Bearer primary-key", "Bearer backup-key",
    ]
    assert g.status_snapshot()["endpoint_count"] == 2


@pytest.mark.asyncio
async def test_failover_does_not_short_circuit_case_when_backup_succeeds(monkeypatch):
    g = LLMFailoverGateway()
    g.configure_endpoints([
        {"base_url": "https://93.184.216.34/v1", "api_key": "bad-key", "model": "m1"},
        {"base_url": "https://93.184.216.35/v1", "api_key": "good-key", "model": "m2"},
    ], fallback_to_env=False)
    seen = _mock_provider(monkeypatch, [401, 200])

    with llm_case_scope() as state:
        assert (await g.chat("s", "u")).content
        assert not state.blocked
    assert len(seen) == 2
