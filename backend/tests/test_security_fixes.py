"""安全修复回归测试：对象级授权 / CORS / 日志脱敏 / Provider 防外带 / Mock 语料相关性。

对应 2026-09-12 测试报告中的 P0/P1 问题；运行方式与其他测试一致：
cd backend && .venv/Scripts/python -m pytest tests/test_security_fixes.py -v
"""
from __future__ import annotations

import asyncio
import logging

import httpx
import pytest

from app.core.auth import hash_password
from app.core.logging import RedactingFilter
from app.core.url_guard import (
    UnsafeURLError,
    ensure_provider_url_basic,
    ensure_safe_provider_url,
    normalize_provider_url,
    provider_origin,
    same_provider_origin,
)
from app.models import Case, User
from app.services.zhihu.mock_data import MockWebSearchProvider, MockZhihuProvider

PASSWORD = "password123"


def _login(client, db_session, username: str, user_id: str | None = None) -> User:
    """确保用户存在并登录，返回该用户。"""
    user = User(
        id=user_id or f"usr_{username}",
        username=username,
        password_hash=hash_password(PASSWORD),
    )
    db_session.add(user)
    db_session.commit()
    r = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert r.status_code == 200
    return user


def _add_private_case(db_session, owner: User, case_id: str = "case_sec1", is_demo: bool = False) -> None:
    db_session.add(
        Case(
            id=case_id, user_id=owner.id, title="私有案件", original_question="q",
            proposition="p", status="verdict_ready", is_public=False, is_demo=is_demo,
            public_id=f"pub_{case_id}",
        )
    )
    db_session.commit()


# ---------- 对象级授权 ----------

def test_private_subresources_require_view_permission(client, db_session):
    owner = _login(client, db_session, "sec_owner", "usr_sec_owner")
    _add_private_case(db_session, owner)
    _login(client, db_session, "sec_stranger", "usr_sec_stranger")

    # 陌生登录用户：所有子资源 404，不泄露存在性
    get_paths = [
        "/api/cases/case_sec1",
        "/api/cases/case_sec1/sources",
        "/api/cases/case_sec1/evidence",
        "/api/cases/case_sec1/verdict",
        "/api/cases/case_sec1/events?after=0",
        "/api/cases/case_sec1/favorite",
    ]
    for path in get_paths:
        assert client.get(path).status_code == 404, path
    assert client.post("/api/cases/case_sec1/favorite").status_code == 404
    assert client.delete("/api/cases/case_sec1/favorite").status_code == 404
    assert client.post(
        "/api/cases/case_sec1/questions", json={"target": "judge", "text": "陌生人的质询内容"}
    ).status_code == 404
    assert client.post("/api/cases/case_sec1/start").status_code == 404

    # 匿名：同样 404
    client.post("/api/auth/logout")
    for path in get_paths:
        assert client.get(path).status_code == 404, path
    assert client.post("/api/cases/case_sec1/start").status_code == 404

    # 属主：可见且可操作（verdict 尚未生成 → 授权通过后返回 404「尚未生成」）
    client.post("/api/auth/login", json={"username": "sec_owner", "password": PASSWORD})
    assert client.get("/api/cases/case_sec1").status_code == 200
    assert client.get("/api/cases/case_sec1/sources").status_code == 200
    assert client.get("/api/cases/case_sec1/evidence").status_code == 200
    assert client.get("/api/cases/case_sec1/events?after=0").status_code == 200
    fav = client.get("/api/cases/case_sec1/favorite")
    assert fav.status_code == 200 and fav.json()["is_favorite"] is False
    v = client.get("/api/cases/case_sec1/verdict")
    assert v.status_code == 404 and v.json()["detail"] == "判决书尚未生成"


def test_private_demo_case_stays_private(client, db_session):
    owner = _login(client, db_session, "demo_owner", "usr_demo_owner")
    _add_private_case(db_session, owner, "case_demosec", is_demo=True)
    _login(client, db_session, "demo_stranger", "usr_demo_stranger")
    # is_demo 不再覆盖 is_public：私有演示案件对陌生人和匿名均不可见
    assert client.get("/api/cases/case_demosec").status_code == 404
    assert client.get("/api/cases/case_demosec/evidence").status_code == 404
    client.post("/api/auth/logout")
    assert client.get("/api/cases/case_demosec").status_code == 404


def test_start_requires_owner_or_admin(client, db_session):
    owner = _login(client, db_session, "start_owner", "usr_start_owner")
    _add_private_case(db_session, owner, "case_startsec")
    _login(client, db_session, "start_stranger", "usr_start_stranger")
    assert client.post("/api/cases/case_startsec/start").status_code == 404
    client.post("/api/auth/logout")
    assert client.post("/api/cases/case_startsec/start").status_code == 404
    # 属主可启动（verdict_ready 早退分支，不触发工作流）
    client.post("/api/auth/login", json={"username": "start_owner", "password": PASSWORD})
    assert client.post("/api/cases/case_startsec/start").status_code == 200


# ---------- CORS ----------

def test_cors_preflight_allows_patch_and_delete(client):
    origin = "http://localhost:5173"
    for method in ("GET", "POST", "PATCH", "DELETE"):
        r = client.options(
            "/api/auth/me",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "content-type",
            },
        )
        assert r.status_code == 200, method
        allow = r.headers.get("access-control-allow-methods", "")
        assert method in allow, (method, allow)
        assert r.headers.get("access-control-allow-credentials") == "true"
    # 未授权来源仍被拒绝
    evil = client.options("/api/auth/me", headers={"Origin": "https://evil.invalid", "Access-Control-Request-Method": "GET"})
    assert evil.status_code == 400
    assert "access-control-allow-origin" not in evil.headers


# ---------- 日志脱敏 ----------

def _redact(message: str) -> str:
    f = RedactingFilter()
    rec = logging.LogRecord("probe", logging.INFO, "", 0, message, (), None)
    f.filter(rec)
    return rec.getMessage()


def test_redaction_covers_json_cookie_and_secret():
    cases = {
        '{"password":"json-secret","api_key":"json-key"}': ["json-secret", "json-key"],
        "Cookie: zhicourt_session=session-secret": ["session-secret"],
        "Set-Cookie: zhicourt_session=set-secret; Path=/": ["set-secret"],
        "access_secret=provider-secret": ["provider-secret"],
        '"Authorization": "Bearer bearer-secret"': ["bearer-secret"],
        "Authorization: Bearer header-secret": ["header-secret"],
        "llm_api_key: sk-abc123": ["sk-abc123"],
        "authorization_code=one-time-code": ["one-time-code"],
        "app_key=oauth-app-key": ["oauth-app-key"],
        "zhicourt_oauth_state=oauth-state-secret": ["oauth-state-secret"],
    }
    for message, secrets in cases.items():
        redacted = _redact(message)
        for secret in secrets:
            assert secret not in redacted, (message, redacted)


def test_redaction_keeps_normal_text():
    text = "user alice created case case_123 in 120ms"
    assert _redact(text) == text


# ---------- URL 防护 ----------

def test_url_guard_blocks_private_targets():
    for bad in ("http://127.0.0.1:8000/v1", "http://169.254.169.254/latest", "http://localhost:11434/v1",
                "ftp://example.com/x", "https://user:pass@example.com/v1", ""):
        try:
            ensure_safe_provider_url(bad)
        except UnsafeURLError:
            pass
        else:
            raise AssertionError(f"should be blocked: {bad!r}")
    assert ensure_safe_provider_url("http://93.184.216.34/v1") == "http://93.184.216.34/v1"


def test_url_guard_basic_check_for_save():
    assert ensure_provider_url_basic("https://api.example.com/v1")
    for bad in (
        "ftp://example.com", "https://user:pass@example.com", "not-a-url", "",
        "https://example.com/v1?api_key=secret", "https://example.com/v1#fragment",
        "https://example.com\\@127.0.0.1/v1",
    ):
        with pytest.raises(UnsafeURLError):
            ensure_provider_url_basic(bad)


def test_url_guard_normalizes_origin_and_default_ports():
    assert normalize_provider_url(" HTTPS://EXAMPLE.COM.:443/v1/ ") == "https://example.com/v1"
    assert provider_origin("https://example.com/a") == "https://example.com"
    assert same_provider_origin("https://EXAMPLE.com:443/v1", "https://example.com/other")
    assert not same_provider_origin("https://example.com", "http://example.com")
    assert not same_provider_origin("https://example.com", "https://example.com:8443")


def test_private_url_opt_in_is_dev_only(monkeypatch):
    monkeypatch.setenv("PROVIDER_ALLOW_PRIVATE_URLS", "true")
    monkeypatch.setenv("APP_ENV", "dev")
    assert ensure_safe_provider_url("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434/v1"
    monkeypatch.setenv("APP_ENV", "prod")
    with pytest.raises(UnsafeURLError):
        ensure_safe_provider_url("http://127.0.0.1:11434/v1")


# ---------- Provider 测试端点防密钥外带 ----------

def _login_admin(client, db_session):
    admin = User(
        id="usr_sec_admin", username="sec_admin", nickname="管理员",
        password_hash=hash_password("adminpass123"), role="admin",
    )
    db_session.add(admin)
    db_session.commit()
    r = client.post("/api/auth/login", json={"username": "sec_admin", "password": "adminpass123"})
    assert r.status_code == 200


def test_llm_test_refuses_new_url_without_fresh_key(client, db_session):
    _login_admin(client, db_session)
    # 先保存一份带密钥的配置
    save = client.post(
        "/api/admin/providers/config",
        json={"fields": {"llm_base_url": "https://api.example.com/v1", "llm_api_key": "sk-stored", "llm_model": "m"}},
    )
    assert save.status_code == 200
    try:
        # 新 Base URL + 不重输 Key → 拒绝且不发起外部请求
        r = client.post(
            "/api/admin/providers/config/test-llm",
            json={"fields": {"llm_base_url": "https://other.example.com/v1"}},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert "重新输入 API Key" in body["error"]
    finally:
        client.post("/api/admin/providers/config", json={"fields": {"llm_base_url": "", "llm_api_key": "", "llm_model": ""}})


def test_llm_test_blocks_private_target(client, db_session):
    _login_admin(client, db_session)
    r = client.post(
        "/api/admin/providers/config/test-llm",
        json={"fields": {"llm_base_url": "http://127.0.0.1:9999/v1", "llm_api_key": "sk-fresh", "llm_model": "m"}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "不允许" in body["error"]


def test_llm_test_uses_bounded_streaming_json_helper(client, db_session, monkeypatch):
    from app.services.llm import gateway
    from app.services.llm.gateway import LLMGateway, LLMResult

    _login_admin(client, db_session)
    before = gateway.status_snapshot()
    calls = []

    async def fake_chat(self, *args, **kwargs):
        calls.append((self is gateway, self.base_url, self.max_response_bytes))
        return LLMResult(content="OK")

    monkeypatch.setattr(LLMGateway, "chat", fake_chat)
    response = client.post(
        "/api/admin/providers/config/test-llm",
        json={"fields": {
            "llm_base_url": "https://93.184.216.34/v1",
            "llm_api_key": "sk-fresh",
            "llm_model": "m",
        }},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert calls == [(False, "https://93.184.216.34/v1", 64 * 1024)]
    assert gateway.status_snapshot() == before


def test_provider_config_save_rejects_unsafe_url(client, db_session):
    _login_admin(client, db_session)
    r = client.post("/api/admin/providers/config", json={"fields": {"llm_base_url": "ftp://example.com/v1"}})
    assert r.status_code == 422


# ---------- 我的案件归属 ----------

def test_me_cases_returns_owned_cases_including_demo(client, db_session):
    _login(client, db_session, "me_cases_user", "usr_me_cases")
    c = client.post("/api/cases", json={"question": "我的案件接口测试问题"}).json()
    # 模拟演示标记（Mock Provider 场景）：自有案件不应因此从「我的案件」消失
    case = db_session.get(Case, c["id"])
    case.is_demo = True
    db_session.commit()

    mine = client.get("/api/auth/me/cases")
    assert mine.status_code == 200
    items = mine.json()
    assert len(items) == 1
    assert items[0]["id"] == c["id"]
    assert items[0]["is_demo"] is True

    # 未登录拒绝
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me/cases").status_code == 401


# ---------- SECRET_ENCRYPTION_KEY 归一 ----------

def test_env_secret_key_accepts_standard_and_passphrase():
    import base64

    from app.core.crypto import _key_from_env

    standard = base64.urlsafe_b64encode(b"a" * 32).decode()
    # 标准 44 字符 base64 key 原样使用
    assert _key_from_env(standard) == standard.encode()
    # 任意口令派生为合法 Fernet 密钥且可初始化
    from cryptography.fernet import Fernet

    for secret in ("short", "x" * 44, "我的口令 Secret"):
        key = _key_from_env(secret)
        assert len(base64.urlsafe_b64decode(key)) == 32
        Fernet(key)  # 不抛出即合法
    # 相同口令派生结果稳定（跨重启可解密）
    assert _key_from_env("stable-passphrase") == _key_from_env("stable-passphrase")


# ---------- Mock 语料与命题相关 ----------

def test_mock_corpus_anchored_to_topic():
    topic = "远程办公是否优于坐班"
    results = asyncio.run(MockZhihuProvider().search(topic, limit=8))
    assert len(results) >= 6
    for r in results:
        assert "远程办公" in r.title
        assert "远程办公" in r.summary
    web = asyncio.run(MockWebSearchProvider().search(topic, limit=6))
    for r in web:
        assert "远程办公" in r.summary
