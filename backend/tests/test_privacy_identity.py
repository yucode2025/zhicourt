from __future__ import annotations

from app.core.auth import ANON_COOKIE, hash_password
from app.models import Case, User, UserQuestion, Verdict, gen_id


def _login(client, db, user_id: str, username: str):
    db.add(User(id=user_id, username=username, password_hash=hash_password("password123"), account_kind="member"))
    db.commit()
    assert client.post("/api/auth/login", json={"username": username, "password": "password123"}).status_code == 200


def test_new_case_private_and_anon_cookie_not_user_id(client, db_session):
    response = client.post("/api/cases", json={"question": "默认隐私应该如何设计？"})
    assert response.status_code == 201
    body = response.json()
    assert body["is_public"] is False
    assert body["public_id"] is None
    token = response.cookies.get(ANON_COOKIE)
    assert token and not token.startswith("usr_")
    case = db_session.get(Case, body["id"])
    assert case is not None and token != case.user_id


def test_formal_user_id_cannot_be_used_as_anon_cookie(client, db_session):
    owner = User(id="usr_victim", username="victim", password_hash=hash_password("password123"), account_kind="member")
    db_session.add(owner)
    db_session.add(Case(id="case_victim", user_id=owner.id, title="私有", original_question="q", proposition="p"))
    db_session.commit()
    client.cookies.set(ANON_COOKIE, owner.id)
    assert client.get("/api/cases/case_victim").status_code == 404
    assert client.post("/api/cases/case_victim/start").status_code == 404


def test_public_case_only_returns_own_questions(client, db_session):
    owner = User(id="usr_owner_q", username="ownerq", password_hash=hash_password("password123"), account_kind="member")
    stranger = User(id="usr_stranger_q", username="strangerq", password_hash=hash_password("password123"), account_kind="member")
    case = Case(id="case_public_q", user_id=owner.id, title="公开", original_question="q", proposition="p", is_public=True)
    db_session.add_all([owner, stranger, case])
    db_session.add_all([
        UserQuestion(id="uq_owner", case_id=case.id, user_id=owner.id, target="judge", text="owner question"),
        UserQuestion(id="uq_stranger", case_id=case.id, user_id=stranger.id, target="judge", text="stranger question"),
    ])
    db_session.commit()

    anonymous = client.get("/api/cases/case_public_q")
    assert anonymous.status_code == 200 and anonymous.json()["user_questions"] == []
    _login(client, db_session, "usr_reader_q", "readerq")
    assert client.get("/api/cases/case_public_q").json()["user_questions"] == []
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"username": "ownerq", "password": "password123"}).status_code == 200
    rows = client.get("/api/cases/case_public_q").json()["user_questions"]
    assert [row["id"] for row in rows] == ["uq_owner"]


def test_publish_unpublish_rotates_share_link(client, db_session):
    _login(client, db_session, "usr_publish", "publisher")
    case = Case(
        id="case_publish", user_id="usr_publish", title="发布", original_question="q", proposition="p",
        status="verdict_ready", is_public=False,
    )
    db_session.add(case)
    db_session.add(Verdict(id=gen_id("vd"), case_id=case.id, conclusion="c"))
    db_session.commit()

    first = client.post("/api/cases/case_publish/publish")
    assert first.status_code == 200
    public_id = first.json()["public_id"]
    assert public_id and client.get(f"/api/share/{public_id}").status_code == 200
    assert client.post("/api/cases/case_publish/unpublish").status_code == 200
    assert client.get(f"/api/share/{public_id}").status_code == 404
    second_id = client.post("/api/cases/case_publish/publish").json()["public_id"]
    assert second_id != public_id


def test_password_utf8_byte_boundary(client):
    ok = client.post("/api/auth/register", json={"username": "bytes_ok", "password": "汉" * 24})
    assert ok.status_code == 201
    too_long = client.post("/api/auth/register", json={"username": "bytes_bad", "password": "汉" * 25})
    assert too_long.status_code == 422
    login = client.post("/api/auth/login", json={"username": "bytes_ok", "password": "😀" * 19})
    assert login.status_code == 401
