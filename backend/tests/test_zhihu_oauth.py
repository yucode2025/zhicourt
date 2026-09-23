"""知乎 OAuth 登录：state 校验、单次消费、账号创建/绑定与错误路径（外部调用全部 Mock）。"""
from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.models import AnonymousSession, Case, User, ZhihuOAuthAccount, ZhihuOAuthState, gen_id, utcnow
from app.services import zhihu_oauth


APP_ID = "hackathon-app-id"
APP_KEY = "hackathon-app-key"
REDIRECT_URI = "http://localhost:5173/login/zhihu"

PROFILE = {
    "uid": 969570047710216200,
    "hash_id": "0e4f7aexample",
    "fullname": "知乎知友",
    "gender": "unknown",
    "headline": "",
    "description": "",
    "avatar_path": "https://picx.zhimg.com/example.jpg",
    "url": "https://openapi.zhihu.com/users/969570047710216200",
    "email": "",
    "phone_no": "",
}


@pytest.fixture(autouse=True)
def _enable_oauth(monkeypatch):
    monkeypatch.setattr(settings, "zhihu_oauth_app_id", APP_ID, raising=False)
    monkeypatch.setattr(settings, "zhihu_oauth_app_key", APP_KEY, raising=False)
    monkeypatch.setattr(settings, "zhihu_oauth_redirect_uri", REDIRECT_URI, raising=False)
    monkeypatch.setattr(settings, "zhihu_oauth_openapi_base", "https://openapi.zhihu.com", raising=False)
    monkeypatch.setattr(zhihu_oauth, "validate_outbound_url", lambda url, **_kwargs: url)


@pytest.fixture()
def _mock_upstream(monkeypatch):
    """Mock 换 Token 与用户信息两个外部调用。"""
    def fake_exchange(code: str):
        assert code == "good-code"
        return zhihu_oauth.ZhihuToken(access_token="oauth-token-xyz", expires_in=3600)

    def fake_profile(token: str):
        assert token == "oauth-token-xyz"
        return zhihu_oauth.ZhihuProfile(
            uid=str(PROFILE["uid"]), hash_id=PROFILE["hash_id"],
            fullname=PROFILE["fullname"], avatar_url=PROFILE["avatar_path"],
        )

    monkeypatch.setattr(zhihu_oauth, "exchange_token", fake_exchange)
    monkeypatch.setattr(zhihu_oauth, "fetch_profile", fake_profile)


def _parse_state(url: str) -> str:
    from urllib.parse import parse_qs, urlsplit

    query = parse_qs(urlsplit(url).query)
    return query["state"][0]


# ---------- 单元：授权 URL 与协议解析 ----------
def test_authorize_url_contains_encoded_params():
    url = zhihu_oauth.build_authorize_url("s" * 43)
    assert url.startswith("https://openapi.zhihu.com/authorize?")
    assert f"app_id={APP_ID}" in url
    assert "response_type=code" in url
    from urllib.parse import quote

    assert f"redirect_uri={quote(REDIRECT_URI, safe='')}" in url
    assert "state=" + "s" * 43 in url


def test_fetch_profile_parses_large_uid(monkeypatch):
    def fake_request(client, method, url, **kwargs):
        assert method == "GET"
        assert kwargs["max_bytes"] == zhihu_oauth.OAUTH_MAX_RESPONSE_BYTES
        assert kwargs["headers"] == {"Authorization": "Bearer tok"}
        return 200, PROFILE

    monkeypatch.setattr(zhihu_oauth, "request_json_sync", fake_request)
    profile = zhihu_oauth.fetch_profile("tok")
    # uid 超出 JS 安全整数范围，必须无损转为字符串
    assert profile.uid == "969570047710216200"
    assert profile.fullname == "知乎知友"


def test_fetch_profile_rejects_missing_uid(monkeypatch):
    monkeypatch.setattr(
        zhihu_oauth, "request_json_sync",
        lambda *args, **kwargs: (200, {"code": 404, "data": "User don't exist"}),
    )
    with pytest.raises(zhihu_oauth.ZhihuOAuthError):
        zhihu_oauth.fetch_profile("tok")


def test_exchange_token_treats_code_20000_as_success(monkeypatch):
    def fake_request(client, method, url, **kwargs):
        assert method == "POST"
        assert kwargs["max_bytes"] == zhihu_oauth.OAUTH_MAX_RESPONSE_BYTES
        assert kwargs["data"]["app_key"] == APP_KEY
        return 200, {"code": 20000, "access_token": "tok", "expires_in": 3600}

    monkeypatch.setattr(zhihu_oauth, "request_json_sync", fake_request)
    token = zhihu_oauth.exchange_token("c")
    assert token.access_token == "tok"


def test_exchange_token_accepts_official_success_without_business_code(monkeypatch):
    monkeypatch.setattr(
        zhihu_oauth,
        "request_json_sync",
        lambda *args, **kwargs: (200, {"access_token": "tok", "expires_in": 3600}),
    )
    assert zhihu_oauth.exchange_token("c").expires_in == 3600


def test_exchange_token_rejects_http_200_business_error_and_bad_expiry(monkeypatch):
    for payload in (
        {"code": 401, "access_token": "tok", "expires_in": 3600},
        {"code": 20000, "access_token": "tok", "expires_in": True},
        {"code": 20000, "access_token": "tok", "expires_in": 0},
    ):
        monkeypatch.setattr(
            zhihu_oauth, "request_json_sync", lambda *args, _payload=payload, **kwargs: (200, _payload)
        )
        with pytest.raises(zhihu_oauth.ZhihuOAuthError):
            zhihu_oauth.exchange_token("c")


def test_exchange_token_allows_missing_expiry_for_one_shot_profile_fetch(monkeypatch):
    monkeypatch.setattr(
        zhihu_oauth,
        "request_json_sync",
        lambda *args, **kwargs: (200, {"code": 20000, "access_token": "tok"}),
    )
    assert zhihu_oauth.exchange_token("c").expires_in is None


def test_fetch_profile_rejects_error_code_even_with_uid(monkeypatch):
    monkeypatch.setattr(
        zhihu_oauth,
        "request_json_sync",
        lambda *args, **kwargs: (200, {"code": 404, "uid": 123}),
    )
    with pytest.raises(zhihu_oauth.ZhihuOAuthError):
        zhihu_oauth.fetch_profile("tok")


def test_fetch_profile_rejects_invalid_int64_uids(monkeypatch):
    for uid in (True, 0, -1, 1.5, "9223372036854775808"):
        monkeypatch.setattr(
            zhihu_oauth,
            "request_json_sync",
            lambda *args, _uid=uid, **kwargs: (200, {"uid": _uid}),
        )
        with pytest.raises(zhihu_oauth.ZhihuOAuthError):
            zhihu_oauth.fetch_profile("tok")


def test_exchange_token_network_error(monkeypatch):
    def boom(*a, **kw):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(zhihu_oauth, "request_json_sync", boom)
    with pytest.raises(zhihu_oauth.ZhihuOAuthError):
        zhihu_oauth.exchange_token("c")


# ---------- 接口：authorize ----------
def test_authorize_disabled_returns_503(client, monkeypatch):
    monkeypatch.setattr(settings, "zhihu_oauth_app_id", "", raising=False)
    resp = client.get("/api/auth/zhihu/authorize")
    assert resp.status_code == 503


def test_authorize_rejects_callback_outside_frontend_origin(client, monkeypatch):
    monkeypatch.setattr(settings, "zhihu_oauth_redirect_uri", "https://evil.example/login/zhihu", raising=False)
    assert client.get("/api/auth/zhihu/authorize").status_code == 503


def test_auth_providers_exposes_only_safe_capability_state(client):
    resp = client.get("/api/auth/providers")
    assert resp.status_code == 200
    assert resp.json() == {"zhihu": {"enabled": True, "callback_path": "/login/zhihu"}}
    assert APP_ID not in resp.text and APP_KEY not in resp.text


def test_authorize_returns_url_and_state(client, db_session):
    resp = client.get("/api/auth/zhihu/authorize")
    assert resp.status_code == 200
    url = resp.json()["authorize_url"]
    assert "openapi.zhihu.com/authorize" in url
    state = _parse_state(url)
    assert len(state) >= 32
    # state cookie 已下发
    assert "zhicourt_oauth_state" in client.cookies
    # 服务端保存的是 state 哈希，且未消费
    row = db_session.get(ZhihuOAuthState, zhihu_oauth.state_hash(state))
    assert row is not None and row.consumed is False
    # 数据库不存 state 明文
    assert state not in {r.id for r in db_session.query(ZhihuOAuthState).all()}


# ---------- 接口：callback ----------
def _callback(client, state: str, code: str = "good-code"):
    return client.post("/api/auth/zhihu/callback", json={"code": code, "state": state})


def test_callback_success_creates_zhihu_user(client, db_session, _mock_upstream):
    state = _parse_state(client.get("/api/auth/zhihu/authorize").json()["authorize_url"])
    resp = _callback(client, state)
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] is True
    assert body["user"]["nickname"] == "知乎知友"
    # OAuth Token 不进入响应
    assert "oauth-token-xyz" not in resp.text
    user = db_session.query(User).filter(User.id == body["user"]["id"]).one()
    assert user.account_kind == "zhihu"
    assert user.username is None and user.password_hash is None
    account = db_session.query(ZhihuOAuthAccount).filter_by(user_id=user.id).one()
    assert account.zhihu_uid == str(PROFILE["uid"])
    assert account.access_token == ""
    assert account.token_expires_at is None
    # 会话已建立
    me = client.get("/api/auth/me").json()
    assert me["user"]["id"] == user.id
    assert client.cookies.get("zhicourt_oauth_state") is None
    # state 已消费
    assert db_session.get(ZhihuOAuthState, zhihu_oauth.state_hash(state)).consumed is True


def test_zhihu_user_is_not_blocked_by_guest_creation_flag(client, db_session, _mock_upstream, monkeypatch):
    state = _parse_state(client.get("/api/auth/zhihu/authorize").json()["authorize_url"])
    assert _callback(client, state).status_code == 200
    monkeypatch.setattr(
        "app.api.routes_cases.get_flag",
        lambda _db, key: False if key == "allow_guest_cases" else True,
    )
    created = client.post("/api/cases", json={"question": "知乎用户创建案件是否正常？"})
    assert created.status_code == 201


def test_callback_same_uid_returns_same_user(client, db_session, _mock_upstream):
    first = _callback(client, _parse_state(client.get("/api/auth/zhihu/authorize").json()["authorize_url"]))
    assert first.status_code == 200
    user_id = first.json()["user"]["id"]
    client.post("/api/auth/logout")

    second = _callback(client, _parse_state(client.get("/api/auth/zhihu/authorize").json()["authorize_url"]))
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["user"]["id"] == user_id
    assert db_session.query(ZhihuOAuthAccount).count() == 1


def test_oauth_token_is_never_persisted(client, db_session, _mock_upstream):
    state = _parse_state(client.get("/api/auth/zhihu/authorize").json()["authorize_url"])
    assert _callback(client, state).status_code == 200
    account = db_session.query(ZhihuOAuthAccount).one()
    assert account.access_token == ""
    assert account.token_expires_at is None
    assert client.post("/api/auth/logout").status_code == 200
    assert account.access_token == ""
    assert account.token_expires_at is None


def test_callback_rejects_state_mismatch(client, db_session):
    client.get("/api/auth/zhihu/authorize")
    resp = _callback(client, "x" * 43)
    assert resp.status_code == 400
    assert resp.json()["detail"]["reason"] == "state_mismatch"
    # 旧标签页的错误 state 不得清除当前浏览器中新发起的有效 state cookie。
    assert not any("zhicourt_oauth_state=" in h for h in resp.headers.get_list("set-cookie"))


def test_callback_rejects_missing_state_cookie(client, db_session):
    client.get("/api/auth/zhihu/authorize")
    client.cookies.delete("zhicourt_oauth_state")
    resp = _callback(client, "y" * 43)
    assert resp.status_code == 400
    assert resp.json()["detail"]["reason"] == "state_mismatch"


def test_callback_rejects_expired_state(client, db_session):
    state = "expired" + "0" * 40
    db_session.add(
        ZhihuOAuthState(
            id=zhihu_oauth.state_hash(state), expires_at=utcnow() - timedelta(seconds=1),
        )
    )
    db_session.commit()
    client.cookies.set("zhicourt_oauth_state", state)
    resp = _callback(client, state)
    assert resp.status_code == 400
    assert resp.json()["detail"]["reason"] == "state_invalid"


def test_callback_rejects_replayed_state(client, db_session, _mock_upstream):
    state = _parse_state(client.get("/api/auth/zhihu/authorize").json()["authorize_url"])
    assert _callback(client, state).status_code == 200
    client.cookies.set("zhicourt_oauth_state", state)
    replay = _callback(client, state)
    assert replay.status_code == 400
    assert replay.json()["detail"]["reason"] == "state_invalid"


def test_callback_upstream_failure_is_502(client, db_session, monkeypatch):
    state = _parse_state(client.get("/api/auth/zhihu/authorize").json()["authorize_url"])

    def fail_exchange(code: str):
        raise zhihu_oauth.ZhihuOAuthError("token_exchange_failed")

    monkeypatch.setattr(zhihu_oauth, "exchange_token", fail_exchange)
    resp = _callback(client, state)
    assert resp.status_code == 502
    # 不产生任何用户
    assert db_session.query(User).filter(User.account_kind == "zhihu").count() == 0


def test_callback_migrates_anonymous_data(client, db_session, _mock_upstream):
    # 先建立游客身份并创建案件
    from app.core.auth import ANON_COOKIE, create_anonymous_identity

    anon, token = create_anonymous_identity(db_session, "127.0.0.1")
    db_session.commit()
    case = Case(
        id=gen_id("case"),
        user_id=anon.id,
        title="游客案件",
        original_question="测试",
        proposition="测试",
        status="created",
    )
    db_session.add(case)
    db_session.commit()
    client.cookies.set(ANON_COOKIE, token, domain="testserver.local", path="/")

    state = _parse_state(client.get("/api/auth/zhihu/authorize").json()["authorize_url"])
    resp = _callback(client, state)
    assert resp.status_code == 200
    assert resp.json()["migrated_cases"] == 1
    db_session.expire_all()  # 迁移走 bulk update，刷新身份缓存
    moved = db_session.get(Case, case.id)
    assert moved.user_id == resp.json()["user"]["id"]
    # 匿名会话被标记为已迁移（TestClient jar 不反映 delete_cookie，查库验证）
    from app.core.auth import _anon_hash

    sess = db_session.get(AnonymousSession, _anon_hash(token))
    assert sess is not None and sess.status == "migrated"


def test_member_binding_is_explicit_and_conflicts_do_not_rebind(
    client, db_session, _mock_upstream,
):
    from app.core.auth import hash_password

    member_a = User(id=gen_id("usr"), username="alice", nickname="A",
                    password_hash=hash_password("password123"), account_kind="member")
    member_b = User(id=gen_id("usr"), username="bob", nickname="B",
                    password_hash=hash_password("password123"), account_kind="member")
    db_session.add_all([member_a, member_b])
    db_session.commit()

    assert client.post(
        "/api/auth/login", json={"username": "alice", "password": "password123"}
    ).status_code == 200
    # 登录入口不能再把已有 Session 隐式解释为绑定。
    assert client.get("/api/auth/zhihu/authorize").status_code == 409
    link = client.post(
        "/api/auth/zhihu/link/authorize", json={"password": "password123"}
    )
    assert link.status_code == 200
    state = _parse_state(link.json()["authorize_url"])
    old_session = client.cookies.get("zhicourt_session")
    bound = _callback(client, state)
    assert bound.status_code == 200
    assert bound.json()["linked"] is True
    assert bound.json()["created"] is False
    assert bound.json()["user"]["id"] == member_a.id
    assert bound.json()["user"]["has_zhihu_oauth"] is True
    assert client.cookies.get("zhicourt_session") != old_session
    assert db_session.query(ZhihuOAuthAccount).filter_by(user_id=member_a.id).count() == 1

    # bob 显式绑定同一知乎账号时必须冲突，且不能改变 alice 的绑定。
    client.post("/api/auth/logout")
    assert client.post(
        "/api/auth/login", json={"username": "bob", "password": "password123"}
    ).status_code == 200
    link2 = client.post(
        "/api/auth/zhihu/link/authorize", json={"password": "password123"}
    )
    state2 = _parse_state(link2.json()["authorize_url"])
    conflict = _callback(client, state2)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["reason"] == "already_bound"
    account = db_session.query(ZhihuOAuthAccount).one()
    assert account.user_id == member_a.id


def test_member_can_claim_prior_empty_oauth_only_account(
    client, db_session, _mock_upstream,
):
    state = _parse_state(client.get("/api/auth/zhihu/authorize").json()["authorize_url"])
    oauth_login = _callback(client, state)
    assert oauth_login.status_code == 200
    oauth_user_id = oauth_login.json()["user"]["id"]

    assert client.post("/api/auth/logout").status_code == 200
    registered = client.post(
        "/api/auth/register",
        json={"username": "registered", "password": "password123"},
    )
    assert registered.status_code == 201
    member_id = registered.json()["user"]["id"]
    link = client.post(
        "/api/auth/zhihu/link/authorize", json={"password": "password123"}
    )
    state = _parse_state(link.json()["authorize_url"])
    claimed = _callback(client, state)

    assert claimed.status_code == 200
    assert claimed.json()["linked"] is True
    assert claimed.json()["transferred"] is True
    assert claimed.json()["user"]["id"] == member_id
    assert db_session.get(User, oauth_user_id).status == "disabled"
    assert db_session.query(ZhihuOAuthAccount).one().user_id == member_id


def test_prepare_link_reloads_target_status_instead_of_trusting_stale_user(db_session):
    from app.core.auth import hash_password

    source = User(
        id=gen_id("usr"), username=None, nickname="OAuth source",
        password_hash=None, role="user", status="active", account_kind="zhihu",
    )
    target = User(
        id=gen_id("usr"), username="stale-target", nickname="Target",
        password_hash=hash_password("password123"), role="user",
        status="active", account_kind="member",
    )
    account = ZhihuOAuthAccount(
        id=gen_id("zho"), user_id=source.id, zhihu_uid=str(PROFILE["uid"]),
    )
    db_session.add_all([source, target, account])
    db_session.commit()

    # 模拟密码复核完成后、OAuth 上游返回前，管理员并发停用目标账号；
    # 绕过 ORM identity map，确保传入对象仍是旧的 active 快照。
    db_session.connection().execute(
        User.__table__.update().where(User.id == target.id).values(status="disabled")
    )
    db_session.commit()
    assert target.status == "active"

    with pytest.raises(zhihu_oauth.ZhihuOAuthError, match="member_account_required"):
        zhihu_oauth.prepare_link_account(db_session, account.zhihu_uid, target.id)
    db_session.rollback()
    db_session.expire_all()
    assert db_session.query(ZhihuOAuthAccount).one().user_id == source.id
    assert db_session.get(User, source.id).status == "active"


def test_member_cannot_auto_claim_oauth_account_with_business_data(
    client, db_session, _mock_upstream,
):
    state = _parse_state(client.get("/api/auth/zhihu/authorize").json()["authorize_url"])
    oauth_login = _callback(client, state)
    oauth_user_id = oauth_login.json()["user"]["id"]
    oauth_case = Case(
        id=gen_id("case"), user_id=oauth_user_id, title="需人工合并的案件",
        original_question="测试", proposition="测试", status="created",
    )
    db_session.add(oauth_case)
    db_session.commit()

    client.post("/api/auth/logout")
    client.post(
        "/api/auth/register",
        json={"username": "registered", "password": "password123"},
    )
    link = client.post(
        "/api/auth/zhihu/link/authorize", json={"password": "password123"}
    )
    state = _parse_state(link.json()["authorize_url"])
    conflict = _callback(client, state)

    assert conflict.status_code == 409
    assert conflict.json()["detail"]["reason"] == "oauth_account_has_data"
    assert db_session.get(User, oauth_user_id).status == "active"
    assert db_session.get(Case, oauth_case.id).user_id == oauth_user_id
    assert db_session.query(ZhihuOAuthAccount).one().user_id == oauth_user_id


def test_transfer_compensation_rejects_business_write_added_mid_transaction(
    client, db_session, _mock_upstream, monkeypatch,
):
    state = _parse_state(client.get("/api/auth/zhihu/authorize").json()["authorize_url"])
    oauth_login = _callback(client, state)
    oauth_user_id = oauth_login.json()["user"]["id"]

    client.post("/api/auth/logout")
    registered = client.post(
        "/api/auth/register",
        json={"username": "compensation", "password": "password123"},
    )
    member_id = registered.json()["user"]["id"]
    link = client.post(
        "/api/auth/zhihu/link/authorize", json={"password": "password123"}
    )
    state = _parse_state(link.json()["authorize_url"])
    original_upsert = zhihu_oauth.upsert_account
    injected_case_id = gen_id("case")

    def inject_source_write(db, user, profile):
        db.add(Case(
            id=injected_case_id,
            user_id=oauth_user_id,
            title="竞争写入",
            original_question="测试",
            proposition="测试",
            status="created",
        ))
        return original_upsert(db, user, profile)

    monkeypatch.setattr(zhihu_oauth, "upsert_account", inject_source_write)
    conflict = _callback(client, state)

    assert conflict.status_code == 409
    assert conflict.json()["detail"]["reason"] == "oauth_account_has_data"
    db_session.expire_all()
    assert db_session.get(Case, injected_case_id) is None
    assert db_session.get(User, oauth_user_id).status == "active"
    assert db_session.query(ZhihuOAuthAccount).one().user_id == oauth_user_id
    assert db_session.get(User, member_id).status == "active"


def test_concurrent_unique_binding_conflict_is_stable_409_and_rolls_back(
    client, db_session, _mock_upstream, monkeypatch,
):
    state = _parse_state(client.get("/api/auth/zhihu/authorize").json()["authorize_url"])
    oauth_login = _callback(client, state)
    oauth_user_id = oauth_login.json()["user"]["id"]

    client.post("/api/auth/logout")
    registered = client.post(
        "/api/auth/register",
        json={"username": "race", "password": "password123"},
    )
    member_id = registered.json()["user"]["id"]
    link = client.post(
        "/api/auth/zhihu/link/authorize", json={"password": "password123"}
    )
    state = _parse_state(link.json()["authorize_url"])

    original_commit = db_session.commit
    commit_count = 0

    def conflict_on_final_commit():
        nonlocal commit_count
        commit_count += 1
        if commit_count == 2:
            raise IntegrityError("concurrent oauth binding", {}, RuntimeError("unique"))
        return original_commit()

    monkeypatch.setattr(db_session, "commit", conflict_on_final_commit)
    conflict = _callback(client, state)

    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {
        "reason": "oauth_binding_conflict",
        "message": "绑定状态已发生变化，请重新发起知乎绑定",
    }
    db_session.expire_all()
    assert db_session.get(User, oauth_user_id).status == "active"
    assert db_session.query(ZhihuOAuthAccount).one().user_id == oauth_user_id
    assert db_session.get(User, member_id).status == "active"


def test_disabled_zhihu_account_cannot_login(client, db_session, _mock_upstream):
    state = _parse_state(client.get("/api/auth/zhihu/authorize").json()["authorize_url"])
    assert _callback(client, state).status_code == 200
    # 管理员禁用该知乎用户后再次登录应被拒绝
    account = db_session.query(ZhihuOAuthAccount).one()
    user = db_session.get(User, account.user_id)
    user.status = "disabled"
    db_session.commit()
    client.post("/api/auth/logout")
    state2 = _parse_state(client.get("/api/auth/zhihu/authorize").json()["authorize_url"])
    resp = _callback(client, state2)
    assert resp.status_code == 403
    assert resp.json()["detail"]["reason"] == "account_disabled"


def test_disabled_user_session_is_rejected(client, db_session, _mock_upstream):
    state = _parse_state(client.get("/api/auth/zhihu/authorize").json()["authorize_url"])
    assert _callback(client, state).status_code == 200
    account = db_session.query(ZhihuOAuthAccount).one()
    db_session.get(User, account.user_id).status = "disabled"
    db_session.commit()
    assert client.get("/api/auth/me").json()["user"] is None
