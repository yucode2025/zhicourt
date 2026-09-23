"""管理后台 API 测试：users / cases / usage / providers / settings / audit / integrity / retry。"""
from __future__ import annotations

from app.core.auth import hash_password
from app.models import Case, Evidence, Source, User, UserQuestion, ZhihuOAuthAccount


def _login_admin(client, db_session, username="adminx", password="adminpass123"):
    from app.models import User as U

    if db_session.query(U).filter(U.username == username).first() is None:
        db_session.add(
            U(id=f"usr_{username}", username=username, password_hash=hash_password(password), role="admin")
        )
        db_session.commit()
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_admin_dashboard_shape(client, db_session):
    _login_admin(client, db_session)
    d = client.get("/api/admin/dashboard").json()
    for key in ("users_total", "cases_total", "cases_running", "cases_failed", "cases_done",
                "questions_today", "trend", "cache_hit_rate", "recent_errors"):
        assert key in d
    assert len(d["trend"]) == 7


def test_admin_users_pagination_and_search(client, db_session):
    for i in range(25):
        db_session.add(User(id=f"usr_u{i:02d}", username=f"user{i:02d}", password_hash=hash_password("password123")))
    db_session.commit()
    _login_admin(client, db_session)
    r = client.get("/api/admin/users?page=1&size=20").json()
    assert r["total"] >= 25
    assert len(r["items"]) == 20
    r2 = client.get("/api/admin/users?q=user07").json()
    assert r2["total"] == 1
    assert r2["items"][0]["username"] == "user07"


def test_admin_lists_and_can_disable_zhihu_users(client, db_session):
    oauth_user = User(
        id="usr_zhihu_admin_view",
        username=None,
        nickname="知乎知友",
        password_hash=None,
        account_kind="zhihu",
    )
    db_session.add(oauth_user)
    db_session.flush()
    account = ZhihuOAuthAccount(
        id="zho_admin_view",
        user_id=oauth_user.id,
        zhihu_uid="969570047710216201",
        access_token="stored-token",
    )
    db_session.add(account)
    db_session.commit()
    _login_admin(client, db_session)

    listing = client.get("/api/admin/users?q=知乎知友")
    assert listing.status_code == 200
    assert listing.json()["items"][0]["account_kind"] == "zhihu"

    disabled = client.post(f"/api/admin/users/{oauth_user.id}/status", json={"status": "disabled"})
    assert disabled.status_code == 200
    assert oauth_user.status == "disabled"
    assert account.access_token == ""


def test_admin_user_disable_enable(client, db_session):
    db_session.add(User(id="usr_tgt", username="target1", password_hash=hash_password("password123")))
    db_session.commit()
    _login_admin(client, db_session)
    r = client.post("/api/admin/users/usr_tgt/status", json={"status": "disabled"})
    assert r.status_code == 200
    # 被禁用用户无法登录
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"username": "target1", "password": "password123"}).status_code == 401
    # 审计日志有记录
    logs = client.get("/api/admin/audit").json()
    _login_admin(client, db_session)
    logs = client.get("/api/admin/audit").json()
    assert any(item["action"] == "USER_DISABLED" for item in logs["items"][:5])


def test_admin_cannot_disable_self(client, db_session):
    _login_admin(client, db_session, "selfadmin")
    admin = db_session.query(User).filter(User.username == "selfadmin").first()
    r = client.post(f"/api/admin/users/{admin.id}/status", json={"status": "disabled"})
    assert r.status_code == 400


def test_admin_cases_filter_and_retry(client, db_session, monkeypatch):
    monkeypatch.setattr("app.api.routes_admin.submit_case", lambda *_args, **_kwargs: "submitted")
    db_session.add(
        Case(id="case_failx", title="失败案件", original_question="q", proposition="p",
             status="failed", error_message="provider down", is_public=True, public_id="pubfx")
    )
    db_session.commit()
    _login_admin(client, db_session)
    r = client.get("/api/admin/cases?status=failed").json()
    assert any(c["id"] == "case_failx" for c in r["items"])
    detail = client.get("/api/admin/cases/case_failx/detail").json()
    assert detail["status"] == "failed"
    # 重试
    retry = client.post("/api/admin/cases/case_failx/retry")
    assert retry.status_code == 200
    assert retry.json()["status"] == "queued"
    # 完成案件不允许重试
    db_session.query(Case).filter(Case.id == "case_failx").update({"status": "verdict_ready"})
    db_session.commit()
    assert client.post("/api/admin/cases/case_failx/retry").status_code == 409


def test_admin_settings_and_flags(client, db_session):
    _login_admin(client, db_session)
    r = client.get("/api/admin/settings").json()
    keys = {s["key"] for s in r["settings"]}
    assert {"allow_guest_cases", "enable_hot_cases", "usage_access_policy"} <= keys
    # Secret 只显示 configured/missing
    for s in r["secrets"]:
        assert s["status"] in ("configured", "missing")
        assert "value" not in s
    # 修改
    ok = client.post("/api/admin/settings/allow_guest_cases", json={"value": False})
    assert ok.status_code == 200
    # 生效：游客（无登录）创建案件被拒
    client.post("/api/auth/logout")
    denied = client.post("/api/cases", json={"question": "游客被禁了吗测试"})
    assert denied.status_code == 403
    # 恢复默认
    _login_admin(client, db_session)
    client.post("/api/admin/settings/allow_guest_cases", json={"value": True})


def test_settings_reject_incorrect_types_and_surface_422(client, db_session):
    _login_admin(client, db_session)
    for key, value in (("enable_web_search", "false"), ("enable_mock_provider", 0),
                       ("evidence_top_k_per_source", True), ("case_input_max_length", 20.5)):
        response = client.post(f"/api/admin/settings/{key}", json={"value": value})
        assert response.status_code == 422
    assert client.post("/api/admin/settings/usage_access_policy", json={"value": "invalid"}).status_code == 422
    assert client.post("/api/admin/settings/enable_web_search", json={"value": False}).status_code == 200
    assert client.get("/api/admin/providers").status_code == 200


def test_admin_api_usage_local_only(client, db_session):
    _login_admin(client, db_session)
    r = client.get("/api/admin/api-usage").json()
    assert "providers" in r and "zhihu_search" in r["providers"]


def test_admin_official_quota_uses_fresh_then_stale_cache(client, db_session, monkeypatch):
    from app.api import routes_admin
    from app.services.provider_config import save_fields
    from app.services.zhihu.client import RealZhihuProvider, ZhihuProviderError

    _login_admin(client, db_session)
    save_fields(db_session, {"zhihu_api_key": "quota-secret"})
    cache: dict[str, object] = {}
    monkeypatch.setattr(routes_admin, "cache_get", lambda key: cache.get(key))
    monkeypatch.setattr(routes_admin, "cache_set", lambda key, value, _ttl: cache.__setitem__(key, value))
    calls = 0
    failing = False

    async def fake_quota(_self):
        nonlocal calls
        calls += 1
        if failing:
            raise ZhihuProviderError("temporary timeout")
        return [{"APIID": "knowledge", "APIName": "知识库", "TotalQuota": 100,
                 "TotalUsed": 2, "RemainingQuota": 98}]

    monkeypatch.setattr(RealZhihuProvider, "quota", fake_quota)
    first = client.get("/api/admin/providers/quota").json()
    second = client.get("/api/admin/providers/quota").json()
    assert first["available"] is True and first["cached"] is False
    assert second["available"] is True and second["cached"] is True
    assert calls == 1

    for key in list(cache):
        if key.endswith(":fresh"):
            del cache[key]
    failing = True
    stale = client.get("/api/admin/providers/quota").json()
    assert stale["available"] is True and stale["cached"] is True and stale["stale"] is True
    assert stale["items"] == first["items"]


def test_admin_providers_show_mock(client, db_session):
    _login_admin(client, db_session)
    r = client.get("/api/admin/providers").json()
    by_key = {p["key"]: p for p in r["items"]}
    # 测试环境无官方 Key → 必须可见 MOCK 模式
    assert by_key["zhihu_search"]["mode"] == "MOCK"
    assert by_key["web_search"]["mode"] == "MOCK"
    assert by_key["llm"]["mode"] == "FALLBACK"
    assert by_key["database"]["status"] in ("HEALTHY", "DOWN")
    assert by_key["redis"]["status"] in ("HEALTHY", "DEGRADED")


def test_integrity_check_detects_unsynchronized_compat_json(client, db_session):
    from sqlalchemy import update
    from app.models import Argument

    case = Case(id="case_intg", title="完整性", original_question="q", proposition="p")
    source = Source(id="src_intg", case_id=case.id, origin="web", title="来源", url="https://example.com")
    evidence = Evidence(id="ev_intg", case_id=case.id, source_id=source.id,
                        claim="证据", stance="pro", evidence_type="opinion")
    argument = Argument(id="arg_intg", case_id=case.id, side="prosecution", title="论证", body="正文")
    db_session.add_all([case, source, evidence, argument])
    db_session.commit()
    # Core SQL 模拟旧程序只写 JSON、漏写规范关系表的损坏状态。
    db_session.execute(update(Argument).where(Argument.id == argument.id).values(evidence_ids=[evidence.id]))
    db_session.commit()
    _login_admin(client, db_session)
    result = client.get("/api/admin/integrity").json()
    assert result["status"] == "FAIL"
    assert any(item["kind"] == "argument_evidence_out_of_sync" for item in result["issues"])


def test_learning_profile_objective_only(client, db_session):
    from app.core.auth import hash_password

    u = User(id="usr_learner", username="learner1", password_hash=hash_password("password123"))
    db_session.add(u)
    db_session.add(Case(id="case_lrn", user_id=u.id, title="学习档案", original_question="q",
                        proposition="p", status="verdict_ready", is_public=True, public_id="publrn"))
    db_session.add(UserQuestion(id="uq1", case_id="case_lrn", user_id=u.id, target="evidence",
                                text="证据够吗", challenge_type="evidence", response="r"))
    db_session.add(UserQuestion(id="uq2", case_id="case_lrn", user_id=u.id, target="judge",
                                text="逻辑对吗", challenge_type="logic", response="r"))
    db_session.commit()
    client.post("/api/auth/login", json={"username": "learner1", "password": "password123"})
    r = client.get("/api/auth/me/learning").json()
    types = {t["type"]: t["count"] for t in r["challenge_types"]}
    assert types.get("evidence") == 1 and types.get("logic") == 1
    # 不得输出伪评分
    assert "score" not in r and "ability" not in r
    assert "质疑" in r["summary"]
