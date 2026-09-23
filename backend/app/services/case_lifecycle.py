"""案件启动、重试、删除与执行 fencing 的集中事务逻辑。

fencing 原则：
- generation：Case.run_generation 标识一次逻辑运行；start/retry 递增。
- lease：条件 UPDATE（DB 权威）认领唯一 token；每次 fenced 写入都在同一事务里
  验证 id + generation + lease_token + running，并顺带续约心跳。
- stale：验证失败时回滚并返回特殊结果（None / StaleGeneration），绝不写入。
"""
from __future__ import annotations

import asyncio
import os
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from sqlalchemy import func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.models import (
    AgentRun, Argument, ArgumentClaim, ArgumentEvidence, Case, CaseRunAttempt, Claim, ClaimEvidence,
    CrossExamEvidence, CrossExamination, Evidence, Favorite, Source, UserQuestion,
    UserQuestionEvidence, Verdict, VerdictCounterEvidence, VerdictStrongestEvidence,
    gen_id,
)

LEASE_TTL_SECONDS = 300
LEASE_RENEW_SECONDS = 300

# last_completed_stage → 下一次应执行的阶段（恢复边界）。
# research / judge / verdict 没有独立持久化产物：research 崩溃回退到 research 重跑，
# judge 结果未持久化，崩溃后必然重跑 judge。
RESUME_SUCCESSOR: dict[str, str] = {
    "plan": "research",
    "rank": "evidence",
    "evidence": "debate",
    "debate": "cross_exam",
    "cross_exam": "judge",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def lease_owner_id() -> str:
    """当前进程的 lease owner 标识（host:pid:boot-uuid）。"""
    return f"{socket.gethostname()}:{os.getpid()}:{_PROCESS_BOOT_ID}"[:80]


_PROCESS_BOOT_ID = uuid.uuid4().hex[:12]


class StaleGeneration(Exception):
    """当前 worker 已失去案件执行权（generation/lease 被夺走），必须停止写入。"""


def clear_case_outputs(db: Session, case: Case, *, keep_questions: bool = False) -> None:
    # bulk delete 不触发 ORM cascade；显式先删规范关系，兼容 SQLite FK ON 与 MariaDB。
    relations = [
        UserQuestionEvidence, VerdictStrongestEvidence, VerdictCounterEvidence,
        CrossExamEvidence, ArgumentClaim, ArgumentEvidence, ClaimEvidence,
    ]
    for model in relations:
        db.query(model).filter(model.case_id == case.id).delete(synchronize_session=False)
    if keep_questions:
        db.query(UserQuestion).filter(UserQuestion.case_id == case.id).update(
            {UserQuestion.related_evidence_ids: []}, synchronize_session=False
        )
    models = [CrossExamination, Argument, Claim, Evidence, Source, Verdict, AgentRun]
    if not keep_questions:
        models.insert(0, UserQuestion)
    for model in models:
        db.query(model).filter(model.case_id == case.id).delete(synchronize_session=False)
    case.plan = None
    case.progress_events = []
    case.progress_index = 0
    case.current_stage = ""
    case.error_message = None
    case.finished_at = None
    db.flush()


def start_generation(db: Session, case: Case, *, retry: bool = False) -> int:
    """开启新的运行 generation：递增代数并重置全部运行态字段。"""
    case.run_generation = (case.run_generation or 0) + 1
    if retry:
        case.retry_count = (case.retry_count or 0) + 1
    case.status = "queued"
    case.current_stage = "queue"
    case.error_message = None
    case.failure_stage = ""
    case.failure_kind = ""
    case.failure_code = ""
    case.resume_from_stage = ""
    case.last_completed_stage = ""
    case.run_started_at = None
    case.lease_token = ""
    case.lease_owner = ""
    case.lease_expires_at = None
    case.lease_heartbeat_at = None
    case.finished_at = None
    db.flush()
    return case.run_generation


def prepare_retry(db: Session, case: Case) -> None:
    if case.status == "running":
        # “审理中”仅在租约已经明确失效时允许人工重试。用条件 UPDATE 在数据库中
        # 原子抢占该行：若原 worker 已经先续约，则 rowcount=0，不能误杀健康任务；
        # 若本事务先抢占，旧 worker 后续的 fenced 写入会因 status/token 变化而失败。
        now = utcnow()
        stale_token = case.lease_token
        result = db.execute(
            update(Case)
            .where(
                Case.id == case.id,
                Case.status == "running",
                or_(
                    Case.lease_token == "",
                    Case.lease_expires_at.is_(None),
                    Case.lease_expires_at <= now,
                ),
            )
            .values(
                status="failed",
                lease_token="",
                lease_owner="",
                lease_expires_at=None,
                lease_heartbeat_at=None,
            )
        )
        if cast("CursorResult[Any]", result).rowcount != 1:
            db.rollback()
            raise ValueError("案件仍在正常审理中，请稍后再试")
        if stale_token:
            db.execute(
                update(CaseRunAttempt)
                .where(
                    CaseRunAttempt.case_id == case.id,
                    CaseRunAttempt.lease_token == stale_token,
                    CaseRunAttempt.status == "running",
                )
                .values(status="interrupted", finished_at=now)
            )
        db.expire(case)
    elif case.status != "failed":
        raise ValueError("只能重试失败或租约已过期的审理中案件")
    clear_case_outputs(db, case)
    # retry 重置执行口径与公开状态：旧产出口径不遗留到新判决。
    case.engine_mode = "heuristic"
    case.is_demo = False
    case.is_public = False
    case.public_id = None
    start_generation(db, case, retry=True)


def delete_case(db: Session, case: Case) -> None:
    if case.status in ("queued", "running"):
        raise ValueError("审理中的案件暂不能删除")
    db.query(Favorite).filter(Favorite.case_id == case.id).delete(synchronize_session=False)
    clear_case_outputs(db, case)
    db.delete(case)
    db.flush()


# ---------- lease / fencing ----------

def _resume_from(last_completed_stage: str | None) -> str:
    if not last_completed_stage:
        return "plan"
    return RESUME_SUCCESSOR.get(last_completed_stage, "plan")


def claim_lease(
    db: Session,
    case_id: str,
    owner: str,
    *,
    ttl_seconds: int = LEASE_TTL_SECONDS,
    force: bool = False,
) -> tuple[str, int, int] | None:
    """条件认领（DB 权威）：返回 (token, generation, attempt) 或 None。

    可认领状态：
    - queued（无论 lease 是否残留，排队即等待被认领）；
    - running 且租约已过期/为空（崩溃恢复，同代续跑）。
    force=True 仅用于确知原持有者已死的场景（如单进程重启恢复）。
    """
    now = utcnow()
    prior = db.execute(select(Case).where(Case.id == case_id)).scalar_one_or_none()
    if prior is None:
        return None
    was_running = prior.status == "running"

    conditions = [Case.status == "queued"]
    if force:
        conditions = [Case.status.in_(("queued", "running"))]
    else:
        conditions = [
            or_(
                Case.status == "queued",
                Case.lease_token == "",
                Case.lease_expires_at.is_(None),
                Case.lease_expires_at < now,
            ),
            Case.status.in_(("queued", "running")),
        ]

    token = gen_id("lease")
    expires = now + timedelta(seconds=ttl_seconds)
    result = db.execute(
        update(Case)
        .where(Case.id == case_id, *conditions)
        .values(
            status="running",
            current_stage="queue",
            error_message=None,
            lease_token=token,
            lease_owner=owner,
            lease_expires_at=expires,
            lease_heartbeat_at=now,
            run_started_at=func.coalesce(Case.__table__.c.run_started_at, now),
        )
    )
    if cast("CursorResult[Any]", result).rowcount != 1:
        db.rollback()
        return None

    case = db.get(Case, case_id)
    assert case is not None
    generation = case.run_generation or 0
    resume_from = _resume_from(case.last_completed_stage) if was_running else ""
    if was_running:
        case.resume_from_stage = resume_from
        # 旧租约已经过期且本次条件认领成功，上一 attempt 不可能再合法写入。
        # 及时封口，避免恢复后长期同时存在多个 running attempt。
        db.execute(
            update(CaseRunAttempt)
            .where(
                CaseRunAttempt.case_id == case_id,
                CaseRunAttempt.status == "running",
                CaseRunAttempt.lease_token != token,
            )
            .values(status="interrupted", finished_at=now)
        )
    last_attempt = db.execute(
        select(func.max(CaseRunAttempt.attempt)).where(CaseRunAttempt.case_id == case_id)
    ).scalar()
    attempt = int(last_attempt or 0) + 1
    db.add(
        CaseRunAttempt(
            case_id=case_id, attempt=attempt, run_generation=generation,
            lease_token=token, lease_owner=owner, status="running",
            resume_from_stage=resume_from, started_at=now,
        )
    )
    db.commit()
    return token, generation, attempt


def release_lease(db: Session, case_id: str, lease_token: str) -> None:
    """未成功派发执行时归还租约：回到 queued 并把 attempt 标记为 interrupted。"""
    now = utcnow()
    db.execute(
        update(Case)
        .where(Case.id == case_id, Case.lease_token == lease_token, Case.status == "running")
        .values(
            status="queued", current_stage="queue",
            lease_token="", lease_owner="",
            lease_expires_at=None, lease_heartbeat_at=None,
        )
    )
    db.execute(
        update(CaseRunAttempt)
        .where(CaseRunAttempt.case_id == case_id, CaseRunAttempt.lease_token == lease_token)
        .values(status="interrupted", finished_at=now)
    )
    db.commit()


def fenced_case(
    db: Session,
    case_id: str,
    generation: int,
    lease_token: str,
    *,
    renew_seconds: int = LEASE_RENEW_SECONDS,
) -> Case | None:
    """fencing 验证 + 续约，必须作为同一事务的第一条语句。

    以条件 UPDATE（读到的是最新已提交状态并持有行锁）验证
    id + generation + lease_token + running，并顺带续约心跳。
    返回 None 表示 stale：调用方必须回滚且不得写入。
    """
    now = utcnow()
    result = db.execute(
        update(Case)
        .where(
            Case.id == case_id,
            Case.run_generation == generation,
            Case.lease_token == lease_token,
            Case.status == "running",
        )
        .values(
            lease_heartbeat_at=now,
            lease_expires_at=now + timedelta(seconds=renew_seconds),
        )
    )
    if cast("CursorResult[Any]", result).rowcount != 1:
        db.rollback()
        return None
    return db.get(Case, case_id)


def classify_failure(exc: BaseException) -> tuple[str, str]:
    """异常 → (failure_kind, failure_code)。code 取异常类名，保证可入库可检索。"""
    name = type(exc).__name__
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        kind = "timeout"
    elif isinstance(exc, (ConnectionError, OSError)):
        kind = "network"
    elif isinstance(exc, ValueError):
        kind = "validation"
    elif isinstance(exc, RuntimeError):
        kind = "runtime"
    else:
        kind = "internal"
    return kind, name[:60]


def mark_run_failed(
    db: Session,
    case_id: str,
    generation: int,
    lease_token: str,
    attempt: int,
    *,
    stage: str,
    kind: str,
    code: str,
    message: str,
) -> bool:
    """fenced 失败写入：状态、失败分类、attempt 终态与 lease 清理在同一事务。

    返回 False 表示 stale（失去执行权），不做任何写入。
    """
    case = fenced_case(db, case_id, generation, lease_token)
    if case is None:
        return False
    now = utcnow()
    case.status = "failed"
    case.current_stage = "failed"
    case.failure_stage = stage[:40]
    case.failure_kind = kind[:30]
    case.failure_code = code[:60]
    case.error_message = (message or "")[:500]
    case.lease_token = ""
    case.lease_owner = ""
    case.lease_expires_at = None
    case.lease_heartbeat_at = None
    db.execute(
        update(CaseRunAttempt)
        .where(CaseRunAttempt.case_id == case_id, CaseRunAttempt.attempt == attempt)
        .values(
            status="failed", finished_at=now,
            failure_stage=stage[:40], failure_kind=kind[:30], failure_code=code[:60],
            error_message=(message or "")[:500],
        )
    )
    db.commit()
    return True


def mark_attempt_finished(db: Session, case_id: str, attempt: int, *, last_stage: str) -> None:
    """成功终态：attempt 置 done（调用方处在最终提交事务内，不再单独 commit）。"""
    db.execute(
        update(CaseRunAttempt)
        .where(CaseRunAttempt.case_id == case_id, CaseRunAttempt.attempt == attempt)
        .values(status="done", finished_at=utcnow(), last_stage=last_stage[:40])
    )
