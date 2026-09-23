"""三档业务访问策略及明确豁免边界。"""
from __future__ import annotations

from app.core.auth import hash_password
from app.models import Case, SystemSetting, User, ZhihuOAuthAccount, gen_id


def _set_policy(db, value: object) -> None:
    row = db.get(SystemSetting, "usage_access_policy")
    if row is None:
        db.add(SystemSetting(key="usage_access_policy", value=value, updated_by="test"))
    else:
        row.value = value
    db.commit()


def _register(client, username: str = "member") -> dict:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": "password123", "nickname": username},
    )
    assert response.status_code == 201
    return response.json()["user"]


def test_guest_policy_preserves_anonymous_business_access(client):
    response = client.get("/api/usage")
    assert response.status_code == 200
    capabilities = client.get("/api/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json()["usage_access_policy"] == "guest"
    assert capabilities.json()["can_use"] is True
    assert capabilities.headers["cache-control"] == "private, no-store"


def test_authenticated_policy_requires_formal_session(client, db_session):
    _set_policy(db_session, "authenticated")
    denied = client.get("/api/usage")
    assert denied.status_code == 401
    assert denied.json()["detail"]["reason"] == "authentication_required"
    capabilities = client.get("/api/capabilities").json()
    assert capabilities["can_use"] is False
    assert capabilities["required_action"] == "login"

    _register(client)
    assert client.get("/api/usage").status_code == 200
    assert client.get("/api/capabilities").json()["can_use"] is True


def test_zhihu_policy_requires_binding_not_account_kind_guess(client, db_session):
    _set_policy(db_session, "zhihu")
    anonymous = client.get("/api/usage")
    assert anonymous.status_code == 401
    assert anonymous.json()["detail"]["reason"] == "authentication_required"

    user_out = _register(client)
    denied = client.get("/api/usage")
    assert denied.status_code == 403
    assert denied.json()["detail"]["reason"] == "zhihu_oauth_required"

    db_session.add(
        ZhihuOAuthAccount(
            id=gen_id("zho"),
            user_id=user_out["id"],
            zhihu_uid="123456789",
            access_token="",
        )
    )
    db_session.commit()
    assert client.get("/api/usage").status_code == 200
    me = client.get("/api/auth/me").json()["user"]
    assert me["account_kind"] == "member"
    assert me["has_zhihu_oauth"] is True


def test_public_auth_health_and_admin_routes_are_exempt(client, db_session):
    _set_policy(db_session, "zhihu")
    case = Case(
        id=gen_id("case"),
        title="公开判例",
        original_question="公开问题",
        proposition="公开命题",
        status="created",
        is_public=True,
        public_id="public-policy-test",
    )
    admin = User(
        id=gen_id("usr"),
        username="policy_admin",
        nickname="管理员",
        password_hash=hash_password("password123"),
        account_kind="member",
        role="admin",
        status="active",
    )
    db_session.add_all([case, admin])
    db_session.commit()

    assert client.get("/api/share/public-policy-test").status_code == 200
    assert client.get("/api/health/live").status_code == 200
    assert client.get("/api/auth/me").status_code == 200

    login = client.post(
        "/api/auth/login",
        json={"username": "policy_admin", "password": "password123"},
    )
    assert login.status_code == 200
    # 管理 API 只受管理员权限保护，不要求管理员绑定知乎，避免策略误配后锁死。
    assert client.get("/api/admin/settings").status_code == 200
    # 管理员作为普通业务用户时仍遵守全局策略。
    assert client.get("/api/usage").status_code == 403


def test_home_discovery_content_remains_public_under_zhihu_policy(
    client, db_session, monkeypatch,
):
    from app.services.system_settings import set_setting

    _set_policy(db_session, "zhihu")
    # 关闭热榜上游调用，只验证公开路由边界。
    set_setting(db_session, "enable_hot_cases", False)

    async def official_content(kind, limit):
        return [{
            "work_id": "1", "title": f"{kind}-{limit}", "description": "",
            "labels": [], "artwork": "", "tab_artwork": "",
        }]

    monkeypatch.setattr(
        "app.api.routes_cases.list_hackathon_content", official_content
    )
    hot = client.get("/api/hot")
    content = client.get("/api/hackathon/content?kind=knowledge&limit=12")

    assert hot.status_code == 200
    assert hot.json()["items"] == []
    assert content.status_code == 200
    assert content.json()["items"][0]["title"] == "knowledge-12"


def test_corrupt_access_policy_fails_closed(client, db_session):
    _set_policy(db_session, "unexpected")
    response = client.get("/api/usage")
    assert response.status_code == 401
    assert client.get("/api/capabilities").json()["usage_access_policy"] == "authenticated"


def test_every_business_api_route_has_global_policy_dependency():
    from fastapi.routing import APIRoute

    from app.api.access_policy import require_usage_access
    from app.main import app

    exempt_exact = {
        "/api/capabilities", "/api/health", "/api/health/live", "/api/health/ready",
        "/api/docs", "/api/openapi.json", "/api/redoc",
    }
    uncovered: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
            continue
        if (
            route.path in exempt_exact
            or route.path.startswith("/api/auth/")
            or route.path.startswith("/api/admin/")
            or route.path.startswith("/api/share/")
        ):
            continue
        dependencies = {dependency.call for dependency in route.dependant.dependencies}
        if require_usage_access not in dependencies:
            uncovered.append(route.path)
    assert uncovered == []
