"""管理员/系统审计日志。禁止记录密码 / API Key / Secret。"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AdminAuditLog

# 允许记录的动作白名单
ACTIONS = {
    "USER_DISABLED", "USER_ENABLED", "USER_ROLE_CHANGED",
    "CASE_RETRIED", "CASE_VIEWED_PRIVATE", "CASE_INTERRUPTED",
    "SETTING_CHANGED", "PROVIDER_TEST",
    "ADMIN_LOGIN",
}


def audit_event(
    db: Session,
    admin_user_id: str,
    action: str,
    target: str = "",
    detail: str = "",
    ip: str = "",
    result: str = "ok",
    *,
    commit: bool = True,
) -> None:
    """不依赖 Request 的审计入口（后台任务/恢复流程使用）；commit=False 时
    由调用方把审计写入与业务写入放在同一事务。"""
    if action not in ACTIONS:
        action = "OTHER"
    db.add(
        AdminAuditLog(
            admin_user_id=admin_user_id,
            action=action,
            target=target[:120],
            detail=detail[:500],
            ip=ip[:45],
            result=result,
        )
    )
    if commit:
        db.commit()


def audit(db: Session, request: Request, admin_user_id: str, action: str, target: str = "", detail: str = "", result: str = "ok") -> None:
    audit_event(
        db, admin_user_id, action, target, detail,
        ip=(request.client.host if request.client else ""),
        result=result,
    )
