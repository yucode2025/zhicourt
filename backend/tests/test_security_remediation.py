"""CSRF、主机校验、数据库 URL 与共享出站响应边界测试。"""
from __future__ import annotations

import pytest
from httpx import AsyncByteStream, ByteStream, Client, MockTransport, Request, Response
from sqlalchemy.engine import make_url

from app.core.auth import ANON_COOKIE, CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
from app.core.config import Settings
from app.services.outbound_security import (
    OutboundSecurityError,
    read_json_response,
    request_json_sync,
    validate_fixed_https_service,
)


class ChunkStream(AsyncByteStream):
    def __init__(self, *chunks: bytes):
        self.chunks = chunks
        self.reads = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk


async def _json_response(*chunks: bytes, headers: dict[str, str] | None = None) -> Response:
    return Response(
        200,
        headers=headers,
        stream=ChunkStream(*chunks),
        request=Request("GET", "https://provider.example/api"),
    )


def test_cookie_less_server_write_allows_missing_browser_headers(client):
    assert client.cookies.get(SESSION_COOKIE) is None
    assert client.cookies.get(ANON_COOKIE) is None

    response = client.server_request(
        "POST",
        "/api/auth/login",
        json={"username": "nobody", "password": "password123"},
    )
    # 进入登录处理并按凭据失败，而不是被 CSRF 中间件拦截。
    assert response.status_code == 401


def test_browser_write_with_evil_origin_is_rejected(client):
    evil = client.post(
        "/api/auth/login",
        json={"username": "nobody", "password": "password123"},
        headers={"Origin": "https://evil.invalid"},
    )
    assert evil.status_code == 403
    assert evil.json()["detail"] == "请求 Origin 不可信"


def test_session_double_submit_and_rotation(client):
    registered = client.post(
        "/api/auth/register",
        json={"username": "csrf_user", "password": "password123"},
    )
    assert registered.status_code == 201
    assert client.cookies.get(SESSION_COOKIE)
    first_token = client.cookies.get(CSRF_COOKIE)
    assert first_token
    csrf_cookie_header = next(
        value for value in registered.headers.get_list("set-cookie")
        if value.startswith(f"{CSRF_COOKIE}=")
    )
    assert "httponly" not in csrf_cookie_header.lower()

    server_style_with_identity = client.server_request(
        "PATCH",
        "/api/auth/me",
        json={"nickname": "blocked"},
    )
    assert server_style_with_identity.status_code == 403
    assert server_style_with_identity.json()["detail"] == "CSRF 校验失败"

    missing = client.patch(
        "/api/auth/me",
        json={"nickname": "blocked"},
        headers={CSRF_HEADER: ""},
    )
    assert missing.status_code == 403
    wrong = client.patch(
        "/api/auth/me",
        json={"nickname": "blocked"},
        headers={CSRF_HEADER: "wrong"},
    )
    assert wrong.status_code == 403
    cross_site = client.patch(
        "/api/auth/me",
        json={"nickname": "blocked"},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert cross_site.status_code == 403

    changed = client.post(
        "/api/auth/me/password",
        json={"old_password": "password123", "new_password": "password456"},
    )
    assert changed.status_code == 200
    assert client.cookies.get(CSRF_COOKIE) != first_token

    logged_out = client.post("/api/auth/logout")
    assert logged_out.status_code == 200
    assert client.cookies.get(SESSION_COOKIE) is None
    assert client.cookies.get(ANON_COOKIE) is None
    assert client.cookies.get(CSRF_COOKIE) is None


def test_anonymous_identity_sets_csrf_and_requires_it_next_time(client):
    created = client.post("/api/cases", json={"question": "匿名 CSRF 应如何防护？"})
    assert created.status_code == 201
    assert client.cookies.get(ANON_COOKIE)
    assert client.cookies.get(CSRF_COOKIE)

    blocked = client.post(
        "/api/plan",
        json={"question": "后续匿名请求必须带令牌吗？"},
        headers={CSRF_HEADER: ""},
    )
    assert blocked.status_code == 403
    assert client.post(
        "/api/plan", json={"question": "自动附加令牌后应通过吗？"}
    ).status_code == 200


def test_trusted_host_keeps_loopback_health(client):
    assert client.get("http://127.0.0.1/api/health/live").status_code == 200
    assert client.get("/api/health/live", headers={"Host": "evil.invalid"}).status_code == 400


def test_fixed_https_service_accepts_only_exact_code_origin():
    assert validate_fixed_https_service(
        "https://api.zhihu.com/km-indep-home/hackathon/v2/story/list",
        origin="https://api.zhihu.com",
    ).startswith("https://api.zhihu.com/")
    with pytest.raises(OutboundSecurityError):
        validate_fixed_https_service("http://api.zhihu.com/path", origin="https://api.zhihu.com")
    with pytest.raises(OutboundSecurityError):
        validate_fixed_https_service("https://evil.example/path", origin="https://api.zhihu.com")


def test_database_url_create_escapes_credentials():
    configured = Settings()
    configured.database_url_override = ""
    configured.db_user = "user@tenant"
    configured.db_password = "p@ss:/?#[]"
    configured.db_host = "db.example"
    configured.db_port = 3307
    configured.db_name = "court"

    parsed = make_url(configured.database_url)
    assert parsed.username == "user@tenant"
    assert parsed.password == "p@ss:/?#[]"
    assert parsed.host == "db.example"
    assert parsed.port == 3307
    assert parsed.query["charset"] == "utf8mb4"


@pytest.mark.asyncio
async def test_outbound_rejects_content_length_before_reading():
    stream = ChunkStream(b"{}")
    response = Response(
        200,
        headers={"Content-Length": "100"},
        stream=stream,
        request=Request("GET", "https://provider.example/api"),
    )
    with pytest.raises(OutboundSecurityError, match="Content-Length"):
        await read_json_response(response, max_bytes=10)
    assert stream.reads == 0


@pytest.mark.asyncio
async def test_outbound_rejects_chunked_overflow_and_json_shape():
    overflow = await _json_response(b'{"value":"', b"x" * 32, b'"}')
    with pytest.raises(OutboundSecurityError, match="大小限制"):
        await read_json_response(overflow, max_bytes=20)

    duplicate = await _json_response(b'{"a":1,"a":2}')
    with pytest.raises(OutboundSecurityError, match="重复字段"):
        await read_json_response(duplicate)

    deep = await _json_response(("[" * 6 + "0" + "]" * 6).encode())
    with pytest.raises(OutboundSecurityError, match="嵌套层级"):
        await read_json_response(deep, max_depth=5, expected_type=(dict, list))

    wrong_top = await _json_response(b"[]")
    with pytest.raises(OutboundSecurityError, match="顶层结构"):
        await read_json_response(wrong_top)


def test_sync_outbound_stream_rejects_oversized_oauth_json():
    transport = MockTransport(
        lambda request: Response(
            200,
            stream=ByteStream(b'{"token":"' + b"x" * 100 + b'"}'),
            request=request,
        )
    )
    with Client(transport=transport) as client:
        with pytest.raises(OutboundSecurityError, match="大小限制"):
            request_json_sync(client, "POST", "https://provider.example/token", max_bytes=32)
