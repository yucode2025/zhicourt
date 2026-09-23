"""认证与用户中心 API。"""
from __future__ import annotations

import asyncio
import hashlib
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth import (
    ANON_COOKIE,
    CSRF_COOKIE,
    SESSION_COOKIE,
    authenticate,
    create_session,
    destroy_session,
    get_user_by_session,
    hash_password,
    get_or_create_anonymous_identity,
    migrate_anonymous_data,
    new_csrf_token,
    register_user,
    resolve_anonymous_user,
    revoke_anonymous_identity,
    validate_password_bytes,
)
from app.core.config import settings
from app.db.session import get_db
from app.models import Case, Evidence, Favorite, User, UserQuestion, Verdict, gen_id, utcnow, ZhihuOAuthState
from app.services import api_usage, zhihu_oauth
from app.services.zhihu_oauth import ZhihuOAuthError

router = APIRouter(prefix="/api/auth", tags=["auth"])

OAUTH_STATE_COOKIE = "zhicourt_oauth_state"


# ---------- Schemas ----------
class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=30)
    password: str = Field(min_length=8, max_length=72)
    nickname: str | None = Field(default=None, max_length=30)

    @field_validator("password")
    @classmethod
    def password_bytes(cls, v: str) -> str:
        return validate_password_bytes(v)

    @field_validator("username")
    @classmethod
    def username_rules(cls, v: str) -> str:
        import re

        v = v.strip()
        if not re.fullmatch(r"[A-Za-z0-9_\u4e00-\u9fa5]+", v):
            raise ValueError("用户名只能包含中文、字母、数字和下划线")
        return v


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=72)


class ProfileUpdate(BaseModel):
    nickname: str | None = Field(default=None, min_length=1, max_length=30)
    avatar: str | None = Field(default=None, max_length=8)


class PasswordChange(BaseModel):
    old_password: str = Field(min_length=1, max_length=72)
    new_password: str = Field(min_length=8, max_length=72)

    @field_validator("new_password")
    @classmethod
    def new_password_bytes(cls, v: str) -> str:
        return validate_password_bytes(v)


AVATAR_PRESETS = ["⚖", "🎓", "🔬", "📚", "🧭", "🔭", "🧠", "🌱"]


# ---------- 依赖 ----------
def get_current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    return get_user_by_session(db, request.cookies.get(SESSION_COOKIE))


def require_user(user: User | None = Depends(get_current_user)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def get_anonymous_user(request: Request, db: Session) -> User | None:
    return resolve_anonymous_user(db, request.cookies.get(ANON_COOKIE))


def get_or_create_anon(request: Request, response: Response, db: Session) -> User:
    """仅在确需持久游客状态的写入口创建匿名身份。"""
    user, token = get_or_create_anonymous_identity(
        db, request.cookies.get(ANON_COOKIE), request.client.host if request.client else ""
    )
    db.commit()
    if token:
        response.set_cookie(
            ANON_COOKIE, token, max_age=3600 * 24 * 90, httponly=True, samesite="lax",
            secure=settings.is_prod,
        )
        _set_csrf_cookie(response)
    return user


def _user_out(u: User, db: Session) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "account_kind": u.account_kind,
        "has_zhihu_oauth": zhihu_oauth.get_account_by_user(db, u.id) is not None,
        "nickname": u.nickname or u.username,
        "avatar": u.avatar or "⚖",
        "role": u.role,
        "created_at": u.created_at.isoformat() + "Z" if u.created_at else None,
        "last_login_at": u.last_login_at.isoformat() + "Z" if u.last_login_at else None,
    }


def _set_csrf_cookie(response: Response) -> None:
    response.set_cookie(
        CSRF_COOKIE, new_csrf_token(), max_age=3600 * 24 * 90, httponly=False,
        samesite="lax", secure=settings.is_prod,
    )


def _set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, session_id, max_age=3600 * 24 * 14, httponly=True,
        samesite="lax", secure=settings.is_prod,
    )
    _set_csrf_cookie(response)


def _invalidate_pending_oauth(request: Request, response: Response, db: Session) -> None:
    pending_state = request.cookies.get(OAUTH_STATE_COOKIE)
    if pending_state:
        db.execute(delete(ZhihuOAuthState).where(
            ZhihuOAuthState.id == zhihu_oauth.state_hash(pending_state)
        ))
        response.delete_cookie(OAUTH_STATE_COOKIE)


# ---------- 注册 ----------
@router.post("/register", status_code=201)
async def register(req: RegisterRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    if api_usage.rate_limit_hit(f"register:{request.client.host if request.client else 'x'}", 20, 3600):
        raise HTTPException(status_code=429, detail="注册过于频繁，请稍后再试")
    if db.query(User).filter(User.username == req.username).first() is not None:
        raise HTTPException(status_code=409, detail="用户名已被使用")
    user = register_user(db, req.username, req.password, req.nickname)
    db.flush()  # 先落库，迁移 UPDATE 才能满足 users 外键约束
    # 游客数据迁移：注册即把当前匿名会话案件/收藏并入
    migrated = migrate_anonymous_data(db, request.cookies.get(ANON_COOKIE, ""), user)
    destroy_session(db, request.cookies.get(SESSION_COOKIE))
    _invalidate_pending_oauth(request, response, db)
    session = create_session(db, user, request.client.host if request.client else "")
    db.commit()
    _set_session_cookie(response, session.id)
    response.delete_cookie(ANON_COOKIE)
    return {"user": _user_out(user, db), "migrated_cases": migrated}


# ---------- 登录 ----------
@router.post("/login")
async def login(req: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "x"
    if api_usage.rate_limit_hit(f"login:{ip}", 30, 600):
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请 10 分钟后再试")
    user = authenticate(db, req.username, req.password)
    if user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    destroy_session(db, request.cookies.get(SESSION_COOKIE))
    _invalidate_pending_oauth(request, response, db)
    session = create_session(db, user, ip)  # Session Rotation
    db.commit()
    _set_session_cookie(response, session.id)
    # 游客数据合并
    migrated = migrate_anonymous_data(db, request.cookies.get(ANON_COOKIE, ""), user)
    db.commit()
    response.delete_cookie(ANON_COOKIE)
    return {"user": _user_out(user, db), "migrated_cases": migrated}


# ---------- 登出 ----------
@router.post("/logout")
async def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    current = get_user_by_session(db, request.cookies.get(SESSION_COOKIE))
    if current is not None:
        zhihu_oauth.clear_token_for_user(db, current.id)
    destroy_session(db, request.cookies.get(SESSION_COOKIE))
    revoke_anonymous_identity(db, request.cookies.get(ANON_COOKIE))
    db.commit()
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(ANON_COOKIE)
    response.delete_cookie(CSRF_COOKIE)
    response.delete_cookie(OAUTH_STATE_COOKIE)
    # 登出使当前浏览器尚未完成的 OAuth 流程失效，避免旧回调重新建立身份。
    pending_state = request.cookies.get(OAUTH_STATE_COOKIE)
    if pending_state:
        db.execute(delete(ZhihuOAuthState).where(
            ZhihuOAuthState.id == zhihu_oauth.state_hash(pending_state)
        ))
    if current is not None:
        db.execute(delete(ZhihuOAuthState).where(
            ZhihuOAuthState.initiating_user_id == current.id
        ))
    db.commit()
    return {"ok": True}


# ---------- 知乎 OAuth 登录 ----------
class ZhihuCallbackRequest(BaseModel):
    code: str = Field(min_length=1, max_length=256)
    state: str = Field(min_length=16, max_length=128)

    @field_validator("code", "state")
    @classmethod
    def reject_blank_oauth_material(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("OAuth 参数不能为空")
        return value


class ZhihuLinkAuthorizeRequest(BaseModel):
    password: str = Field(min_length=1, max_length=72)


def _session_hash(session_id: str | None) -> str | None:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest() if session_id else None


def _oauth_fail(
    status: int,
    reason: str,
    message: str,
    *,
    request: Request,
    attempted_state: str | None = None,
):
    # 失败标签页只能清理仍属于自己的 cookie，不能覆盖另一标签页新发起的 state。
    resp = JSONResponse(status_code=status, content={"detail": {"reason": reason, "message": message}})
    cookie_state = request.cookies.get(OAUTH_STATE_COOKIE)
    if attempted_state is None or cookie_state == attempted_state:
        resp.delete_cookie(OAUTH_STATE_COOKIE)
    resp.headers["Cache-Control"] = "private, no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@router.get("/providers")
async def auth_providers(db: Session = Depends(get_db)):
    """仅暴露登录能力开关；不向前端返回 App ID、App Key 或回调密钥。"""
    return {
        "zhihu": {
            "enabled": zhihu_oauth.oauth_config_enabled(zhihu_oauth.config_from_db(db)),
            "callback_path": settings.zhihu_oauth_callback_path,
        }
    }


def _start_oauth(
    request: Request,
    response: Response,
    db: Session,
    *,
    purpose: str,
    current: User | None,
    anonymous_token: str | None = None,
) -> dict:
    oauth_config = zhihu_oauth.config_from_db(db)
    if not zhihu_oauth.oauth_config_enabled(oauth_config):
        raise HTTPException(status_code=503, detail="知乎登录未配置")
    ip = request.client.host if request.client else "x"
    if api_usage.rate_limit_hit(f"zhihu_{purpose}_authorize:{ip}", 20, 600):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    state = zhihu_oauth.new_state()
    previous_state = request.cookies.get(OAUTH_STATE_COOKIE)
    if previous_state:
        db.execute(delete(ZhihuOAuthState).where(
            ZhihuOAuthState.id == zhihu_oauth.state_hash(previous_state)
        ))
    db.execute(delete(ZhihuOAuthState).where(
        (ZhihuOAuthState.expires_at < utcnow()) | ZhihuOAuthState.consumed.is_(True)
    ))
    db.add(ZhihuOAuthState(
        id=zhihu_oauth.state_hash(state),
        expires_at=utcnow() + timedelta(seconds=zhihu_oauth.STATE_TTL_SECONDS),
        ip=ip[:45], purpose=purpose,
        initiating_user_id=current.id if current else None,
        initiating_session_hash=_session_hash(request.cookies.get(SESSION_COOKIE)),
        anonymous_session_hash=(hashlib.sha256(anonymous_token.encode("utf-8")).hexdigest()
                                if anonymous_token else None),
    ))
    db.commit()
    response.set_cookie(
        OAUTH_STATE_COOKIE, state, max_age=zhihu_oauth.STATE_TTL_SECONDS,
        httponly=True, samesite="lax", secure=settings.is_prod,
    )
    response.headers["Cache-Control"] = "private, no-store"
    with zhihu_oauth.use_config(oauth_config):
        authorize_url = zhihu_oauth.build_authorize_url(state)
    return {"authorize_url": authorize_url}


@router.get("/zhihu/authorize")
async def zhihu_authorize(request: Request, response: Response, db: Session = Depends(get_db)):
    current = get_user_by_session(db, request.cookies.get(SESSION_COOKIE))
    if current is not None:
        raise HTTPException(
            status_code=409,
            detail={"reason": "already_logged_in", "message": "当前已登录，请使用绑定知乎账号"},
        )
    return _start_oauth(request, response, db, purpose="login", current=None,
                        anonymous_token=request.cookies.get(ANON_COOKIE))


@router.post("/zhihu/link/authorize")
async def zhihu_link_authorize(
    req: ZhihuLinkAuthorizeRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    current = get_user_by_session(db, request.cookies.get(SESSION_COOKIE))
    if current is None or current.account_kind != "member":
        raise HTTPException(
            status_code=401,
            detail={"reason": "local_account_required", "message": "请先登录本地账户"},
        )
    from app.core.auth import verify_password
    if not verify_password(req.password, current.password_hash):
        raise HTTPException(
            status_code=401,
            detail={"reason": "password_verification_failed", "message": "密码复核失败"},
        )
    return _start_oauth(request, response, db, purpose="link", current=current)


@router.post("/zhihu/callback")
async def zhihu_callback(
    req: ZhihuCallbackRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    oauth_config = zhihu_oauth.config_from_db(db)
    if not zhihu_oauth.oauth_config_enabled(oauth_config):
        raise HTTPException(status_code=503, detail="知乎登录未配置")
    ip = request.client.host if request.client else "x"
    if api_usage.rate_limit_hit(f"zhihu_callback:{ip}", 20, 600):
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")

    cookie_state = request.cookies.get(OAUTH_STATE_COOKIE)
    if not cookie_state or not secrets.compare_digest(cookie_state, req.state):
        return _oauth_fail(400, "state_mismatch", "登录状态校验失败，请重新发起知乎登录",
                           request=request, attempted_state=req.state)

    state_row = db.get(ZhihuOAuthState, zhihu_oauth.state_hash(req.state))
    if (
        state_row is None
        or state_row.consumed
        or state_row.expires_at < utcnow()
        or state_row.purpose not in {"login", "link"}
    ):
        db.rollback()
        return _oauth_fail(400, "state_invalid", "登录请求已过期或无效，请重新发起知乎登录",
                           request=request, attempted_state=req.state)

    purpose = state_row.purpose
    initiating_user_id = state_row.initiating_user_id
    initiating_session_hash = state_row.initiating_session_hash
    anonymous_session_hash = state_row.anonymous_session_hash
    # 原子消费 state：从这一刻起，任何上下文或上游失败都不能恢复该授权流程。
    consumed = db.execute(
        update(ZhihuOAuthState)
        .where(
            ZhihuOAuthState.id == zhihu_oauth.state_hash(req.state),
            ZhihuOAuthState.consumed.is_(False),
            ZhihuOAuthState.expires_at >= utcnow(),
        )
        .values(consumed=True)
    )
    if consumed.rowcount != 1:
        db.rollback()
        return _oauth_fail(400, "state_invalid", "登录请求已过期或无效，请重新发起知乎登录",
                           request=request, attempted_state=req.state)
    db.commit()

    session_id = request.cookies.get(SESSION_COOKIE)
    current = get_user_by_session(db, session_id)
    if purpose == "link":
        if (
            current is None
            or current.account_kind != "member"
            or current.id != initiating_user_id
            or initiating_session_hash != _session_hash(session_id)
        ):
            return _oauth_fail(401, "link_context_mismatch", "绑定上下文已失效，请重新验证密码",
                               request=request, attempted_state=req.state)
    else:
        anonymous_token = request.cookies.get(ANON_COOKIE)
        callback_anon_hash = (
            hashlib.sha256(anonymous_token.encode("utf-8")).hexdigest()
            if anonymous_token else None
        )
        if current is not None or callback_anon_hash != anonymous_session_hash:
            return _oauth_fail(409, "login_context_mismatch", "登录上下文已改变，请重新发起知乎登录",
                               request=request, attempted_state=req.state)

    try:
        with zhihu_oauth.use_config(oauth_config):
            token = await asyncio.to_thread(zhihu_oauth.exchange_token, req.code)
            profile = await asyncio.to_thread(zhihu_oauth.fetch_profile, token.access_token)
    except ZhihuOAuthError as exc:
        db.rollback()
        return _oauth_fail(502, str(exc), "知乎授权失败，请稍后重试",
                           request=request, attempted_state=req.state)

    linked = purpose == "link"
    created = False
    transferred = False
    transfer_guard = None
    try:
        if linked:
            try:
                user, transfer_guard, transferred = zhihu_oauth.prepare_link_account(
                    db, profile.uid, current.id, session_id
                )
            except ZhihuOAuthError as exc:
                db.rollback()
                reason = str(exc)
                if reason == "oauth_account_has_data":
                    message = "该知乎登录账号已有案件或收藏，不能自动合并，请联系管理员处理"
                elif reason == "account_disabled":
                    message = "该知乎账号对应的登录账号已被管理员停用"
                elif reason == "member_account_required":
                    message = "本站账号状态已变化，请重新登录后绑定"
                elif reason == "link_context_mismatch":
                    message = "绑定会话已失效，请重新验证密码"
                else:
                    message = "该知乎账号已绑定其他本站账号"
                return _oauth_fail(409, reason, message,
                                   request=request, attempted_state=req.state)
        else:
            try:
                user, created = zhihu_oauth.provision_user(db, profile)
            except ZhihuOAuthError as exc:
                db.rollback()
                return _oauth_fail(403, str(exc), "该知乎账号无法登录",
                                   request=request, attempted_state=req.state)

        try:
            zhihu_oauth.upsert_account(db, user, profile)
        except ZhihuOAuthError as exc:
            db.rollback()
            return _oauth_fail(409, str(exc), "该账号已绑定其他知乎身份",
                               request=request, attempted_state=req.state)
        user.last_login_at = utcnow()

        destroy_session(db, session_id)
        session = create_session(db, user, ip)
        if linked:
            # 绑定成功后轮换本站 Session，消除密码复核前会话的固定风险。
            migrated = 0
        else:
            migrated = migrate_anonymous_data(db, request.cookies.get(ANON_COOKIE, ""), user)
        if transfer_guard is not None:
            try:
                zhihu_oauth.validate_transfer_before_commit(db, transfer_guard)
            except ZhihuOAuthError as exc:
                db.rollback()
                reason = str(exc)
                message = (
                    "该知乎登录账号已有案件或收藏，不能自动合并，请联系管理员处理"
                    if reason == "oauth_account_has_data"
                    else "绑定状态已发生变化，请重新发起知乎绑定"
                )
                return _oauth_fail(409, reason, message,
                                   request=request, attempted_state=req.state)
        db.commit()
    except IntegrityError:
        # 最终以 user_id/zhihu_uid 唯一约束兜底所有并发竞争；不泄露数据库细节。
        db.rollback()
        return _oauth_fail(
            409,
            "oauth_binding_conflict",
            "绑定状态已发生变化，请重新发起知乎绑定",
            request=request,
            attempted_state=req.state,
        )
    _set_session_cookie(response, session.id)
    if not linked:
        response.delete_cookie(ANON_COOKIE)
    if request.cookies.get(OAUTH_STATE_COOKIE) == req.state:
        response.delete_cookie(OAUTH_STATE_COOKIE)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return {
        "user": _user_out(user, db),
        "created": created,
        "linked": linked,
        "transferred": transferred,
        "migrated_cases": migrated,
    }


# ---------- 当前用户 ----------
@router.get("/me")
async def me(user: User | None = Depends(get_current_user), db: Session = Depends(get_db)):
    if user is None:
        return {"user": None}
    return {"user": _user_out(user, db)}


# ---------- 资料修改 ----------
@router.patch("/me")
async def update_profile(
    req: ProfileUpdate,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if req.nickname is not None:
        user.nickname = req.nickname.strip() or user.nickname
    if req.avatar is not None:
        if req.avatar not in AVATAR_PRESETS:
            raise HTTPException(status_code=422, detail="头像需从预设中选择")
        user.avatar = req.avatar
    db.commit()
    return {"user": _user_out(user, db)}


# ---------- 修改密码 ----------
@router.post("/me/password")
async def change_password(
    req: PasswordChange,
    request: Request,
    response: Response,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    from app.core.auth import verify_password

    if not verify_password(req.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="原密码不正确")
    user.password_hash = hash_password(req.new_password)
    # 修改密码后轮换会话
    destroy_session(db, request.cookies.get(SESSION_COOKIE))
    session = create_session(db, user, request.client.host if request.client else "")
    db.commit()
    _set_session_cookie(response, session.id)
    return {"ok": True}


# ---------- 用户中心统计 ----------
@router.get("/me/overview")
async def user_overview(user: User = Depends(require_user), db: Session = Depends(get_db)):
    base = db.query(Case).filter(Case.user_id == user.id)
    total = base.count()
    done = base.filter(Case.status == "verdict_ready").count()
    running = base.filter(Case.status.in_(["running", "queued"])).count()
    failed = base.filter(Case.status == "failed").count()
    questions = db.query(UserQuestion).filter(UserQuestion.user_id == user.id).count()
    favorites = db.query(Favorite).filter(Favorite.user_id == user.id).count()
    return {
        "total_cases": total,
        "done_cases": done,
        "running_cases": running,
        "failed_cases": failed,
        "questions": questions,
        "favorites": favorites,
    }


# ---------- 我的案件（严格按归属，含标记为演示的自有案件） ----------
@router.get("/me/cases")
async def my_cases(user: User = Depends(require_user), db: Session = Depends(get_db)):
    rows = (
        db.query(Case)
        .filter(Case.user_id == user.id)
        .order_by(Case.created_at.desc())
        .limit(100)
        .all()
    )
    return [
        {
            "id": c.id,
            "title": c.title,
            "proposition": c.proposition,
            "status": c.status,
            "is_demo": c.is_demo,
            "is_public": c.is_public,
            "public_id": c.public_id,
            "created_at": c.created_at.isoformat() + "Z" if c.created_at else None,
        }
        for c in rows
    ]


# ---------- 我的质询 ----------
@router.get("/me/questions")
async def my_questions(
    limit: int = 50,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    rows = (
        db.query(UserQuestion, Case.title)
        .join(Case, Case.id == UserQuestion.case_id)
        .filter(UserQuestion.user_id == user.id)
        .order_by(UserQuestion.created_at.desc())
        .limit(min(max(limit, 1), 100))
        .all()
    )
    return [
        {
            "id": q.id, "case_id": q.case_id, "case_title": title,
            "target": q.target, "text": q.text, "response": q.response,
            "challenge_type": q.challenge_type,
            "created_at": q.created_at.isoformat() + "Z" if q.created_at else None,
        }
        for q, title in rows
    ]


# ---------- 我的判决书（最近/收藏） ----------
@router.get("/me/verdicts")
async def my_verdicts(
    list_type: str = "recent", limit: int = 20,
    user: User = Depends(require_user), db: Session = Depends(get_db),
):
    case_ids_q = db.query(Case.id).filter(Case.user_id == user.id)
    if list_type == "favorites":
        fav_ids = db.query(Favorite.case_id).filter(Favorite.user_id == user.id)
        case_ids_q = case_ids_q.filter(Case.id.in_(fav_ids))
    rows = (
        db.query(Case, Verdict, Favorite)
        .join(Verdict, Verdict.case_id == Case.id)
        .outerjoin(Favorite, (Favorite.case_id == Case.id) & (Favorite.user_id == user.id))
        .filter(Case.id.in_(case_ids_q))
        .order_by(Verdict.created_at.desc())
        .limit(min(limit, 50))
        .all()
    )
    return [
        {
            "case_id": c.id,
            "title": c.title,
            "proposition": c.proposition,
            "conclusion_stance": v.conclusion_stance,
            "confidence": v.confidence,
            "created_at": v.created_at.isoformat() + "Z" if v.created_at else None,
            "is_favorite": f is not None,
        }
        for c, v, f in rows
    ]


# ---------- 学习档案（客观行为统计，不做伪评分） ----------
@router.get("/me/learning")
async def learning_profile(user: User = Depends(require_user), db: Session = Depends(get_db)):
    cases = db.query(Case).filter(Case.user_id == user.id, Case.status == "verdict_ready").all()
    topics = [
        {"case_id": c.id, "title": c.title, "proposition": c.proposition, "created_at": c.created_at.isoformat() + "Z"}
        for c in cases[-20:]
    ]
    q_rows = (
        db.query(UserQuestion.challenge_type, func.count(UserQuestion.id))
        .filter(UserQuestion.user_id == user.id)
        .group_by(UserQuestion.challenge_type)
        .all()
    )
    type_names = {"fact": "事实质疑", "logic": "逻辑质疑", "evidence": "证据质疑",
                  "definition": "定义质疑", "scope": "范围质疑", "source": "来源质疑", "other": "一般质询"}
    challenge_types = [
        {"type": t, "label": type_names.get(t, t), "count": n} for t, n in q_rows
    ]
    challenge_types.sort(key=lambda x: -x["count"])
    top = f"你最常质疑的是「{challenge_types[0]['label']}」。" if challenge_types else "还没有质询记录。完成一次审理并质询后，这里会显示你的质疑习惯。"
    return {"topics": topics, "challenge_types": challenge_types, "summary": top}
