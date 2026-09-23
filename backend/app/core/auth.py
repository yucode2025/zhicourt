"""认证与身份服务：bcrypt、正式会话与匿名随机会话。"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta

import bcrypt
from sqlalchemy import select

from app.models import AnonymousSession, Case, Favorite, User, UserSession, gen_id, utcnow

SESSION_COOKIE = "zhicourt_session"
ANON_COOKIE = "zhicourt_sid"
CSRF_COOKIE = "zhicourt_csrf"
CSRF_HEADER = "X-CSRF-Token"
SESSION_TTL_DAYS = 14
ANON_TTL_DAYS = 90


def new_csrf_token() -> str:
    """生成供 double-submit cookie 使用的高熵 CSRF 令牌。"""
    return secrets.token_urlsafe(32)


def csrf_tokens_match(cookie_token: str | None, header_token: str | None) -> bool:
    """常量时间比较 CSRF cookie 与请求头，空值永不通过。"""
    return bool(cookie_token and header_token and secrets.compare_digest(cookie_token, header_token))


def validate_password_bytes(password: str) -> str:
    if len(password.encode("utf-8")) > 72:
        raise ValueError("密码的 UTF-8 编码不能超过 72 字节")
    return password


def hash_password(password: str) -> str:
    validate_password_bytes(password)
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("utf-8")


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash or len(password.encode("utf-8")) > 72:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_session(db, user: User, ip: str = "") -> UserSession:
    session = UserSession(
        id=uuid.uuid4().hex + uuid.uuid4().hex[:16],
        user_id=user.id,
        ip=ip[:45],
        expires_at=utcnow() + timedelta(days=SESSION_TTL_DAYS),
    )
    db.add(session)
    return session


def get_user_by_session(db, session_id: str | None) -> User | None:
    if not session_id:
        return None
    sess = db.get(UserSession, session_id)
    if sess is None or sess.expires_at < utcnow():
        return None
    user = db.get(User, sess.user_id)
    if user is None or user.status != "active":
        return None
    # 匿名用户无凭据不可持有正式会话；知乎 OAuth 用户无本地密码但为正式身份
    if user.password_hash is None and getattr(user, "account_kind", "member") != "zhihu":
        return None
    return user


def destroy_session(db, session_id: str | None) -> None:
    if session_id and (sess := db.get(UserSession, session_id)) is not None:
        db.delete(sess)


def _anon_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _is_anonymous_user(user: User | None) -> bool:
    return bool(
        user is not None
        and user.status == "active"
        and user.password_hash is None
        and user.username is None
        and getattr(user, "account_kind", "anonymous") == "anonymous"
    )


def resolve_anonymous_user(db, token: str | None) -> User | None:
    """只解析新随机令牌，不创建身份，也绝不把 User ID 当 bearer token。"""
    if not token:
        return None
    sess = db.get(AnonymousSession, _anon_hash(token))
    if sess is None or sess.status != "active" or sess.expires_at < utcnow():
        return None
    user = db.get(User, sess.user_id)
    return user if _is_anonymous_user(user) else None


def create_anonymous_identity(db, ip: str = "", user: User | None = None) -> tuple[User, str]:
    user = user if _is_anonymous_user(user) else User(
        id=gen_id("usr"), account_kind="anonymous", role="user", status="active"
    )
    if user not in db:
        db.add(user)
        db.flush()
    token = secrets.token_urlsafe(32)
    db.add(
        AnonymousSession(
            id=_anon_hash(token), user_id=user.id, ip=ip[:45],
            expires_at=utcnow() + timedelta(days=ANON_TTL_DAYS), status="active",
        )
    )
    db.flush()
    return user, token


def get_or_create_anonymous_identity(db, cookie_value: str | None, ip: str = "") -> tuple[User, str | None]:
    """返回匿名用户；第二项仅在需设置/轮换 Cookie 时包含新 token。"""
    user = resolve_anonymous_user(db, cookie_value)
    if user is not None:
        return user, None

    # 一次性兼容旧 Cookie：只接受可证明是匿名账号的 User，正式账号 ID 永不接受。
    legacy = db.get(User, cookie_value) if cookie_value else None
    if _is_anonymous_user(legacy):
        existing = db.execute(select(AnonymousSession).where(AnonymousSession.user_id == legacy.id)).scalar_one_or_none()
        if existing is None:
            return create_anonymous_identity(db, ip, legacy)
    return create_anonymous_identity(db, ip)


def revoke_anonymous_identity(db, cookie_value: str | None, migrated_to: str | None = None) -> None:
    if not cookie_value:
        return
    sess = db.get(AnonymousSession, _anon_hash(cookie_value))
    if sess is not None:
        sess.status = "migrated" if migrated_to else "revoked"
        sess.migrated_to_user_id = migrated_to


def register_user(db, username: str, password: str, nickname: str | None = None) -> User:
    user = User(
        id=gen_id("usr"), username=username, nickname=nickname or username,
        password_hash=hash_password(password), role="user", account_kind="member",
        last_login_at=utcnow(),
    )
    db.add(user)
    return user


def authenticate(db, username: str, password: str) -> User | None:
    user = db.query(User).filter(User.username == username).one_or_none()
    if user is None or user.status != "active" or user.password_hash is None:
        return None
    if not verify_password(password, user.password_hash):
        return None
    user.last_login_at = utcnow()
    return user


def migrate_anonymous_data(db, anon_token: str, target_user: User) -> int:
    """把有效匿名会话的数据幂等合并到正式用户。"""
    anon = resolve_anonymous_user(db, anon_token)
    if anon is None or anon.id == target_user.id:
        return 0
    moved = db.query(Case).filter(Case.user_id == anon.id).update(
        {"user_id": target_user.id}, synchronize_session="fetch"
    )
    existing = {
        f.case_id for f in db.execute(select(Favorite).where(Favorite.user_id == target_user.id)).scalars()
    }
    for favorite in db.execute(select(Favorite).where(Favorite.user_id == anon.id)).scalars().all():
        if favorite.case_id in existing:
            db.delete(favorite)
        else:
            favorite.user_id = target_user.id
            existing.add(favorite.case_id)
            moved += 1
    revoke_anonymous_identity(db, anon_token, target_user.id)
    return moved


def seed_admin_from_env(db) -> str:
    import os

    username = os.getenv("ADMIN_USERNAME", "").strip()
    password = os.getenv("ADMIN_PASSWORD", "").strip()
    if not username or not password:
        return "skipped: ADMIN_USERNAME / ADMIN_PASSWORD not set"
    validate_password_bytes(password)
    existing = db.query(User).filter(User.role == "admin").first()
    if existing is not None:
        return f"skipped: admin exists ({existing.username})"
    if db.query(User).filter(User.username == username).first() is not None:
        return f"skipped: username taken ({username})"
    admin = User(
        id=gen_id("usr"), username=username, nickname="管理员",
        password_hash=hash_password(password), role="admin", account_kind="member",
    )
    db.add(admin)
    db.commit()
    return f"created admin: {username}"
