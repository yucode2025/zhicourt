"""认证 / 权限 / 收藏 / 游客迁移 测试。"""
from __future__ import annotations

from app.core.auth import hash_password
from app.models import Case, User


def _register(client, username="alice", password="password123"):
    return client.post("/api/auth/register", json={"username": username, "password": password})


def test_register_login_logout(client, db_session):
    r = _register(client)
    assert r.status_code == 201
    body = r.json()
    assert body["user"]["username"] == "alice"
    assert "zhicourt_session" in r.cookies

    # 登录状态
    me = client.get("/api/auth/me")
    assert me.json()["user"]["username"] == "alice"

    # 重复注册被拒
    dup = _register(client)
    assert dup.status_code == 409

    # 登出
    out = client.post("/api/auth/logout")
    assert out.status_code == 200
    assert client.get("/api/auth/me").json()["user"] is None

    # 重新登录
    lg = client.post("/api/auth/login", json={"username": "alice", "password": "password123"})
    assert lg.status_code == 200
    # 错误密码 → 统一提示
    bad = client.post("/api/auth/login", json={"username": "alice", "password": "wrongpass99"})
    assert bad.status_code == 401
    assert bad.json()["detail"] == "用户名或密码错误"
    # 不存在的用户 → 同样提示（不泄露存在性）
    nope = client.post("/api/auth/login", json={"username": "ghost", "password": "whatever123"})
    assert nope.json()["detail"] == "用户名或密码错误"


def test_password_hashed_in_db(client, db_session):
    _register(client, "bob")
    u = db_session.query(User).filter(User.username == "bob").first()
    assert u.password_hash is not None
    assert u.password_hash != "password123"
    assert u.password_hash.startswith("$2")  # bcrypt


def test_guest_migration_on_register(client, db_session):
    # 游客先创建案件（匿名 cookie 建立）
    c = client.post("/api/cases", json={"question": "游客的案件问题是什么体验"})
    assert c.status_code == 201
    case_id = c.json()["id"]
    # 注册 → 自动迁移
    r = _register(client, "carol")
    assert r.status_code == 201
    assert r.json()["migrated_cases"] >= 1
    # 用户中心能看到案件
    overview = client.get("/api/auth/me/overview")
    assert overview.json()["total_cases"] >= 1
    # 幂等：再次登录合并不会报错
    client.post("/api/auth/logout")
    lg = client.post("/api/auth/login", json={"username": "carol", "password": "password123"})
    assert lg.status_code == 200
    assert lg.json()["migrated_cases"] == 0


def test_profile_update_and_password_change(client):
    _register(client, "dave")
    # 改昵称 + 头像（预设内）
    r = client.patch("/api/auth/me", json={"nickname": "小戴", "avatar": "🎓"})
    assert r.json()["user"]["nickname"] == "小戴"
    assert r.json()["user"]["avatar"] == "🎓"
    # 非预设头像被拒
    bad = client.patch("/api/auth/me", json={"avatar": "<script>"})
    assert bad.status_code == 422
    # 改密码
    pw = client.post("/api/auth/me/password", json={"old_password": "password123", "new_password": "newpass45678"})
    assert pw.status_code == 200
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"username": "dave", "password": "password123"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "dave", "password": "newpass45678"}).status_code == 200


def test_favorites_flow(client, db_session):
    _register(client, "erin")
    c = client.post("/api/cases", json={"question": "收藏测试用的问题"}).json()
    # 判决书不存在也能收藏案件（收藏对象是案件/判决书页）
    r = client.post(f"/api/cases/{c['id']}/favorite")
    assert r.status_code == 201
    # 重复收藏幂等
    client.post(f"/api/cases/{c['id']}/favorite")
    st = client.get(f"/api/cases/{c['id']}/favorite").json()
    assert st["is_favorite"] is True
    # 列表出现在收藏
    favs = client.get("/api/auth/me/verdicts?list_type=favorites")
    assert favs.status_code == 200
    # 取消
    client.delete(f"/api/cases/{c['id']}/favorite")
    assert client.get(f"/api/cases/{c['id']}/favorite").json()["is_favorite"] is False


def test_user_cannot_access_admin(client):
    _register(client, "frank")
    r = client.get("/api/admin/dashboard")
    assert r.status_code == 403
    r2 = client.get("/api/admin/users")
    assert r2.status_code == 403
    # 未登录同样拒绝
    client.post("/api/auth/logout")
    assert client.get("/api/admin/dashboard").status_code == 401


def test_admin_access_and_audit(client, db_session):
    admin = User(
        id="usr_admin1", username="root1", nickname="管理员",
        password_hash=hash_password("adminpass123"), role="admin",
    )
    db_session.add(admin)
    db_session.commit()
    lg = client.post("/api/auth/login", json={"username": "root1", "password": "adminpass123"})
    assert lg.status_code == 200
    assert client.get("/api/admin/dashboard").status_code == 200
    assert client.get("/api/admin/users").status_code == 200
    assert client.get("/api/admin/audit").status_code == 200


def test_private_case_hidden_from_others(client, db_session):
    owner = User(id="usr_owner", username="owner1", password_hash=hash_password("password123"))
    stranger = User(id="usr_stranger", username="stranger1", password_hash=hash_password("password123"))
    db_session.add_all([owner, stranger])
    db_session.add(
        Case(id="case_priv1", user_id=owner.id, title="私密", original_question="q", proposition="p",
             is_public=False, public_id="pub_priv1")
    )
    db_session.commit()

    client.post("/api/auth/login", json={"username": "stranger1", "password": "password123"})
    # 他人访问私有案件 → 404（不泄露存在性）
    assert client.get("/api/cases/case_priv1").status_code == 404
    # 分享接口也不可见
    assert client.get("/api/share/pub_priv1").status_code == 404
    # 属主可见
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"username": "owner1", "password": "password123"})
    assert client.get("/api/cases/case_priv1").status_code == 200


def test_admin_private_case_access_is_audited(client, db_session):
    owner = User(id="usr_private_owner", username="privateowner", password_hash=hash_password("password123"))
    admin = User(
        id="usr_private_admin", username="privateadmin", password_hash=hash_password("password123"), role="admin"
    )
    db_session.add_all([owner, admin])
    db_session.add(
        Case(
            id="case_private_audit", user_id=owner.id, title="需审计的私有案件",
            original_question="q", proposition="p", is_public=False,
        )
    )
    db_session.commit()
    assert client.post(
        "/api/auth/login", json={"username": "privateadmin", "password": "password123"}
    ).status_code == 200

    assert client.get("/api/cases/case_private_audit").status_code == 200
    assert client.get("/api/cases/case_private_audit/events?after=0").status_code == 200
    # 管理员的私案写操作必须走专用 admin 接口，避免绕过敏感操作审计。
    assert client.post("/api/cases/case_private_audit/start").status_code == 404
    assert client.post(
        "/api/cases/case_private_audit/questions",
        json={"target": "judge", "text": "管理员不应写入他人的私有案件"},
    ).status_code == 404
    from app.models import AdminAuditLog

    log = db_session.query(AdminAuditLog).filter_by(
        admin_user_id=admin.id, action="CASE_VIEWED_PRIVATE", target="case_private_audit"
    ).one()
    assert log.result == "ok"


def test_share_public_case_readonly(client, db_session):
    u = User(id="usr_sharer", username="sharer1", password_hash=hash_password("password123"))
    db_session.add(u)
    db_session.add(
        Case(id="case_pub1", user_id=u.id, title="公开案件", original_question="q", proposition="p",
             status="verdict_ready", is_public=True, public_id="pub_ok1")
    )
    db_session.commit()
    client.post("/api/auth/logout")
    r = client.get("/api/share/pub_ok1")
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "公开案件"
    # 分享输出不含用户私人字段
    assert "user_questions" not in body
    assert "owner" not in body
