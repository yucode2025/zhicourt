"""业务 API 的全局访问策略依赖。"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.auth import SESSION_COOKIE, get_user_by_session
from app.db.session import get_db
from app.models import User
from app.services import zhihu_oauth
from app.services.system_settings import get_setting


# 首页只读发现内容不消耗案件审理能力，也不包含用户私有数据；访问策略只限制业务操作。
PUBLIC_DISCOVERY_PATHS = frozenset({"/api/hot", "/api/hackathon/content"})


@dataclass(frozen=True)
class UsageAccess:
    policy: str
    user: User | None
    allowed: bool


def resolve_usage_access(request: Request, db: Session) -> UsageAccess:
    """只解析正式登录身份；绝不因门禁检查创建游客或其他持久状态。"""
    policy = str(get_setting(db, "usage_access_policy"))
    user = get_user_by_session(db, request.cookies.get(SESSION_COOKIE))
    if policy == "guest":
        allowed = True
    elif policy == "zhihu":
        allowed = user is not None and zhihu_oauth.get_account_by_user(db, user.id) is not None
    else:
        # 未知持久值由 system_settings 收口到 authenticated；这里再次 fail closed。
        policy = "authenticated"
        allowed = user is not None
    return UsageAccess(policy=policy, user=user, allowed=allowed)


def require_usage_access(
    request: Request,
    db: Session = Depends(get_db),
) -> UsageAccess:
    access = resolve_usage_access(request, db)
    if request.method in {"GET", "HEAD"} and request.url.path in PUBLIC_DISCOVERY_PATHS:
        return UsageAccess(policy=access.policy, user=access.user, allowed=True)
    if access.allowed:
        return access
    if access.policy == "zhihu":
        if access.user is None:
            raise HTTPException(
                status_code=401,
                detail={"reason": "authentication_required", "message": "请先登录"},
            )
        raise HTTPException(
            status_code=403,
            detail={"reason": "zhihu_oauth_required", "message": "请先绑定知乎账号后使用此功能"},
        )
    raise HTTPException(
        status_code=401,
        detail={"reason": "authentication_required", "message": "请先登录"},
    )
