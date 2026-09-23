"""知乎 OAuth 登录服务（黑客松接入）。

协议来源：zhihu skill references/oauth.md 与 hackathon-oauth.md。
- 授权页：GET {openapi}/authorize?redirect_uri=&app_id=&response_type=code&state=
- 换 Token：POST {openapi}/access_token（表单 code 字段；回调实测参数名是 authorization_code）
- 用户信息：GET {openapi}/user（Bearer access_token；uid 为 int64，以字符串保存）
"""
from __future__ import annotations

import hashlib
import secrets
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

import httpx
from sqlalchemy import select

from app.core.config import _origin, settings
from app.models import User, ZhihuOAuthAccount, gen_id, utcnow
from app.services.outbound_security import (
    OutboundSecurityError,
    request_json_sync,
    validate_outbound_url,
)

AUTHORIZE_PATH = "/authorize"
TOKEN_PATH = "/access_token"
USER_PATH = "/user"
STATE_TTL_SECONDS = 600
TOKEN_TIMEOUT = 15.0
OAUTH_MAX_RESPONSE_BYTES = 64 * 1024


class ZhihuOAuthError(Exception):
    """对外只暴露安全文案，原始错误只进日志。"""


@dataclass
class ZhihuProfile:
    uid: str
    hash_id: str
    fullname: str
    avatar_url: str


@dataclass
class ZhihuToken:
    access_token: str
    expires_in: int | None


@dataclass(frozen=True)
class OAuthRuntimeConfig:
    app_id: str
    app_key: str
    redirect_uri: str


@dataclass(frozen=True)
class OAuthTransferGuard:
    """提交前再次核验绑定转移所需的不可变标识。"""

    account_id: str
    source_user_id: str
    target_user_id: str


_runtime_config: ContextVar[OAuthRuntimeConfig | None] = ContextVar(
    "zhihu_oauth_runtime_config", default=None
)


def default_config() -> OAuthRuntimeConfig:
    return OAuthRuntimeConfig(
        app_id=settings.zhihu_oauth_app_id,
        app_key=settings.zhihu_oauth_app_key,
        redirect_uri=settings.zhihu_oauth_redirect_uri,
    )


def config_from_db(db) -> OAuthRuntimeConfig:
    from app.services.provider_config import oauth_config

    return OAuthRuntimeConfig(**oauth_config(db))


def redirect_uri_valid(redirect_uri: str) -> bool:
    try:
        parsed = urlsplit(redirect_uri.strip())
        origin = _origin(f"{parsed.scheme}://{parsed.netloc}")
    except (TypeError, ValueError):
        return False
    return bool(
        parsed.path == settings.zhihu_oauth_callback_path
        and not parsed.query
        and not parsed.fragment
        and parsed.username is None
        and parsed.password is None
        and origin in settings.cors_origins
        and (not settings.is_prod or parsed.scheme == "https")
    )


def oauth_config_enabled(config: OAuthRuntimeConfig) -> bool:
    return bool(
        config.app_id and config.app_key and config.redirect_uri
        and redirect_uri_valid(config.redirect_uri)
    )


@contextmanager
def use_config(config: OAuthRuntimeConfig):
    """Bind immutable OAuth credentials to one request/task (and its to_thread calls)."""
    token = _runtime_config.set(config)
    try:
        yield
    finally:
        _runtime_config.reset(token)


def _config() -> OAuthRuntimeConfig:
    return _runtime_config.get() or default_config()


def new_state() -> str:
    return secrets.token_urlsafe(32)


def state_hash(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def build_authorize_url(state: str) -> str:
    config = _config()
    base = settings.zhihu_oauth_openapi_base
    redirect = quote(config.redirect_uri, safe="")
    return (
        f"{base}{AUTHORIZE_PATH}"
        f"?redirect_uri={redirect}&app_id={quote(config.app_id, safe='')}"
        f"&response_type=code&state={quote(state, safe='')}"
    )


def exchange_token(code: str) -> ZhihuToken:
    """用授权码换 access token；app_key 只在后端使用，不出现在 URL/日志。"""
    code = code.strip()
    if not code:
        raise ZhihuOAuthError("token_exchange_failed")
    config = _config()
    form = {
        "app_id": config.app_id,
        "app_key": config.app_key,
        "grant_type": "authorization_code",
        "redirect_uri": config.redirect_uri,
        "code": code,
    }
    base = settings.zhihu_oauth_openapi_base
    try:
        url = validate_outbound_url(f"{base}{TOKEN_PATH}", allowed_origins=[base])
        with httpx.Client(timeout=TOKEN_TIMEOUT, follow_redirects=False) as client:
            status_code, payload = request_json_sync(
                client, "POST", url, data=form, max_bytes=OAUTH_MAX_RESPONSE_BYTES
            )
    except (httpx.HTTPError, OutboundSecurityError) as exc:
        raise ZhihuOAuthError("token_exchange_failed") from exc
    if status_code != 200 or not isinstance(payload, dict):
        raise ZhihuOAuthError("token_exchange_failed")
    # 官方成功示例可能省略业务 code；一旦出现，则只接受历史实测成功码 20000。
    business_code = payload.get("code")
    if business_code is not None and (type(business_code) is not int or business_code != 20000):
        raise ZhihuOAuthError("token_exchange_failed")
    token = payload.get("access_token")
    if not isinstance(token, str) or not token.strip() or len(token) > 2048:
        raise ZhihuOAuthError("token_exchange_failed")
    expires_in = payload.get("expires_in")
    if expires_in is not None and (type(expires_in) is not int or expires_in <= 0):
        raise ZhihuOAuthError("token_exchange_failed")
    return ZhihuToken(access_token=token.strip(), expires_in=expires_in)


def fetch_profile(access_token: str) -> ZhihuProfile:
    """读取授权用户基础信息；uid 以字符串无损保存。"""
    base = settings.zhihu_oauth_openapi_base
    try:
        url = validate_outbound_url(f"{base}{USER_PATH}", allowed_origins=[base])
        with httpx.Client(timeout=TOKEN_TIMEOUT, follow_redirects=False) as client:
            status_code, payload = request_json_sync(
                client,
                "GET",
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                max_bytes=OAUTH_MAX_RESPONSE_BYTES,
            )
    except (httpx.HTTPError, OutboundSecurityError) as exc:
        raise ZhihuOAuthError("profile_fetch_failed") from exc
    if status_code != 200 or not isinstance(payload, dict):
        raise ZhihuOAuthError("profile_fetch_failed")
    if payload.get("code") not in (None, 20000):
        raise ZhihuOAuthError("profile_fetch_failed")
    uid = payload.get("uid")
    if isinstance(uid, bool) or not isinstance(uid, (str, int)):
        raise ZhihuOAuthError("profile_fetch_failed")
    # uid 是正 int64；以十进制字符串落库，避免 JavaScript 安全整数截断。
    uid_str = str(uid).strip()
    if not uid_str.isdigit():
        raise ZhihuOAuthError("profile_fetch_failed")
    try:
        uid_value = int(uid_str)
    except ValueError as exc:
        raise ZhihuOAuthError("profile_fetch_failed") from exc
    if uid_value <= 0 or uid_value > 9_223_372_036_854_775_807:
        raise ZhihuOAuthError("profile_fetch_failed")
    uid_str = str(uid_value)
    hash_id = payload.get("hash_id")
    fullname = payload.get("fullname")
    avatar_url = payload.get("avatar_path")
    return ZhihuProfile(
        uid=uid_str,
        hash_id=hash_id[:80] if isinstance(hash_id, str) else "",
        fullname=fullname.strip()[:120] if isinstance(fullname, str) else "",
        avatar_url=avatar_url.strip()[:500] if isinstance(avatar_url, str) else "",
    )


def get_account_by_uid(db, uid: str) -> ZhihuOAuthAccount | None:
    return db.execute(
        select(ZhihuOAuthAccount).where(ZhihuOAuthAccount.zhihu_uid == uid)
    ).scalar_one_or_none()


def get_account_by_user(db, user_id: str) -> ZhihuOAuthAccount | None:
    return db.execute(
        select(ZhihuOAuthAccount).where(ZhihuOAuthAccount.user_id == user_id)
    ).scalar_one_or_none()


def _locked_account_by_uid(db, uid: str) -> ZhihuOAuthAccount | None:
    return db.execute(
        select(ZhihuOAuthAccount)
        .where(ZhihuOAuthAccount.zhihu_uid == uid)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _locked_users(db, *user_ids: str) -> dict[str, User]:
    ids = sorted(set(user_ids))
    if not ids:
        return {}
    rows = db.execute(
        select(User)
        .where(User.id.in_(ids))
        .order_by(User.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalars().all()
    return {row.id: row for row in rows}


def _locked_account_by_user(db, user_id: str) -> ZhihuOAuthAccount | None:
    return db.execute(
        select(ZhihuOAuthAccount)
        .where(ZhihuOAuthAccount.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _lock_and_assert_no_business_data(db, source_user_id: str) -> None:
    """锁住空结果的 user_id 索引区间，阻止迁移期间插入新业务数据。"""
    from app.models import Case, Favorite, UserQuestion

    for model in (Case, Favorite, UserQuestion):
        row = db.execute(
            select(model.id)
            .where(model.user_id == source_user_id)
            .limit(1)
            .with_for_update()
        ).first()
        if row is not None:
            raise ZhihuOAuthError("oauth_account_has_data")


def prepare_link_account(
    db, uid: str, target_user_id: str, initiating_session_id: str | None = None,
) -> tuple[User, OAuthTransferGuard | None, bool]:
    """锁定并重查绑定两端，必要时转移一个确实为空的 OAuth 自动账号。"""
    from app.models import UserSession

    account = _locked_account_by_uid(db, uid)
    source_user_id = account.user_id if account is not None else None
    users = _locked_users(
        db, target_user_id, *([source_user_id] if source_user_id else [])
    )
    target = users.get(target_user_id)
    if (
        target is None
        or target.status != "active"
        or target.account_kind != "member"
        or not target.password_hash
    ):
        raise ZhihuOAuthError("member_account_required")
    if initiating_session_id is not None:
        initiating_session = db.execute(
            select(UserSession)
            .where(UserSession.id == initiating_session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if (
            initiating_session is None
            or initiating_session.user_id != target.id
            or initiating_session.expires_at < utcnow()
        ):
            raise ZhihuOAuthError("link_context_mismatch")

    # 目标 User 行锁序列化“多个知乎身份同时绑定同一本站账号”的竞争；
    # 唯一约束仍是最终兜底，并由 HTTP 层稳定映射为 409。
    target_binding = _locked_account_by_user(db, target.id)
    if target_binding is not None and (
        account is None or target_binding.id != account.id
    ):
        raise ZhihuOAuthError("different_account_already_bound")
    if account is None or account.user_id == target.id:
        return target, None, False

    source = users.get(account.user_id)
    if source is None:
        raise ZhihuOAuthError("account_not_found")
    if source.status != "active":
        raise ZhihuOAuthError("account_disabled")
    if not (
        source.account_kind == "zhihu"
        and source.role == "user"
        and source.username is None
        and source.password_hash is None
    ):
        raise ZhihuOAuthError("already_bound")

    _lock_and_assert_no_business_data(db, source.id)
    db.query(UserSession).filter(UserSession.user_id == source.id).delete(
        synchronize_session=False
    )
    account.user_id = target.id
    source.status = "disabled"
    db.flush()
    guard = OAuthTransferGuard(
        account_id=account.id,
        source_user_id=source.id,
        target_user_id=target.id,
    )
    return target, guard, True


def validate_transfer_before_commit(db, guard: OAuthTransferGuard) -> None:
    """补偿检查同一事务内后续写入，失败时由调用方整体回滚。"""
    db.flush()
    account = db.execute(
        select(ZhihuOAuthAccount)
        .where(ZhihuOAuthAccount.id == guard.account_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    users = _locked_users(db, guard.source_user_id, guard.target_user_id)
    source = users.get(guard.source_user_id)
    target = users.get(guard.target_user_id)
    target_binding = _locked_account_by_user(db, guard.target_user_id)
    if (
        account is None
        or target_binding is None
        or target_binding.id != guard.account_id
        or account.user_id != guard.target_user_id
    ):
        raise ZhihuOAuthError("oauth_binding_conflict")
    if (
        target is None
        or target.status != "active"
        or target.account_kind != "member"
        or not target.password_hash
    ):
        raise ZhihuOAuthError("member_account_required")
    if source is None or source.status != "disabled":
        raise ZhihuOAuthError("oauth_binding_conflict")
    _lock_and_assert_no_business_data(db, guard.source_user_id)


def transfer_empty_oauth_account(db, account: ZhihuOAuthAccount, target: User) -> None:
    """将无业务数据的 OAuth 自动账号绑定转给已验证的本站账号。

    调用方必须已经完成本站密码复核、会话绑定 state 校验和本次知乎授权。
    有案件、收藏或质询的账号不会自动合并，避免静默改变数据归属。
    """
    locked_target, guard, transferred = prepare_link_account(
        db, account.zhihu_uid, target.id
    )
    if transferred and guard is not None:
        validate_transfer_before_commit(db, guard)
    if locked_target.id != target.id:
        raise ZhihuOAuthError("oauth_binding_conflict")


def upsert_account(db, user: User, profile: ZhihuProfile) -> ZhihuOAuthAccount:
    """绑定或更新知乎身份元数据；OAuth token 只在内存中使用，绝不入库。"""
    account = get_account_by_user(db, user.id)
    if account is not None and account.zhihu_uid != profile.uid:
        # 服务层也拒绝隐式换绑，不能依赖 HTTP router 的预检查。
        raise ZhihuOAuthError("different_account_already_bound")
    existing = get_account_by_uid(db, profile.uid)
    if existing is not None and existing.user_id != user.id:
        raise ZhihuOAuthError("already_bound")
    if account is None:
        account = existing or ZhihuOAuthAccount(
            id=gen_id("zho"), user_id=user.id, zhihu_uid=profile.uid,
        )
        if existing is None:
            db.add(account)
    account.zhihu_uid = profile.uid
    account.zhihu_hash_id = profile.hash_id
    account.zhihu_nickname = profile.fullname or account.zhihu_nickname
    account.zhihu_avatar_url = profile.avatar_url or account.zhihu_avatar_url
    account.access_token = ""
    account.token_expires_at = None
    return account


def clear_token_for_user(db, user_id: str) -> None:
    """退出登录时移除可代表用户访问知乎的凭证，保留账号绑定关系。"""
    account = get_account_by_user(db, user_id)
    if account is not None:
        account.access_token = ""
        account.token_expires_at = None


def provision_user(db, profile: ZhihuProfile) -> tuple[User, bool]:
    """按知乎 uid 找到或创建用户；返回 (用户, 是否新建)。"""
    account = get_account_by_uid(db, profile.uid)
    if account is not None:
        user = db.get(User, account.user_id)
        if user is not None and user.status == "active":
            return user, False
        raise ZhihuOAuthError("account_disabled")
    user = User(
        id=gen_id("usr"), username=None, nickname=(profile.fullname or "知乎用户")[:50],
        role="user", account_kind="zhihu", status="active", last_login_at=utcnow(),
    )
    db.add(user)
    db.flush()
    return user, True
