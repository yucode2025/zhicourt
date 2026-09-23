"""案件执行管理器：有界 FIFO 队列与 DB 权威的条件租约认领。

- 活动键为 (case_id, run_generation)：旧 generation 的失败/退出永远不会阻塞新
  generation 的提交（修复 failed 任务旧 _active 未释放导致永久 queued 的问题）。
- 认领是 DB 权威的条件 UPDATE（queued，或 running 且租约已过期/为空）：
  同一案件在任意进程/线程内同时只可能有一个有效租约持有者。
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.services.case_lifecycle import (
    LEASE_TTL_SECONDS, claim_lease, lease_owner_id, release_lease, utcnow,
)

logger = get_logger(__name__)

SubmitResult = Literal["submitted", "already_active", "queue_full", "submit_failed"]
_executor: ThreadPoolExecutor | None = None
_lock = threading.Lock()
# (case_id, run_generation)：generation 变更后旧键自动失效。
_active: set[tuple[str, int]] = set()
_QUEUE_CAPACITY = max(2, max(1, settings.max_concurrent_cases) * 4)
_slots = threading.BoundedSemaphore(_QUEUE_CAPACITY)
RECOVERY_SWEEP_INTERVAL_SECONDS = max(10, min(60, LEASE_TTL_SECONDS // 3))
_sweeper_lock = threading.Lock()
_sweeper_stop: threading.Event | None = None
_sweeper_thread: threading.Thread | None = None


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(
                max_workers=max(1, settings.max_concurrent_cases), thread_name_prefix="trial"
            )
        return _executor


def running_count() -> int:
    with _lock:
        return len(_active)


def _spawn(case_id: str, generation: int, lease_token: str, owner: str, attempt: int) -> SubmitResult:
    """为已持有的租约派发执行线程；失败时必须归还租约。"""
    key = (case_id, generation)
    with _lock:
        if not _slots.acquire(blocking=False):
            return "queue_full"
        _active.add(key)

    def _wrap() -> None:
        from app.workflows.trial import TrialWorkflow

        try:
            TrialWorkflow(
                case_id, generation=generation, lease_token=lease_token,
                owner=owner, attempt=attempt,
            ).run()
        except Exception:  # noqa: BLE001
            logger.exception("trial thread crashed for case %s", case_id)
        finally:
            with _lock:
                _active.discard(key)
            _slots.release()

    try:
        _get_executor().submit(_wrap)
    except Exception:  # noqa: BLE001
        with _lock:
            _active.discard(key)
        _slots.release()
        logger.exception("trial submission failed for case %s", case_id)
        return "submit_failed"
    return "submitted"


def submit_case(db: Session, case_id: str) -> SubmitResult:
    """提交案件：条件认领租约（DB 权威），认领失败即视为已有活动任务。"""
    owner = lease_owner_id()
    claimed = claim_lease(db, case_id, owner)
    if claimed is None:
        return "already_active"
    lease_token, generation, attempt = claimed
    result = _spawn(case_id, generation, lease_token, owner, attempt)
    if result in ("queue_full", "submit_failed"):
        # 未派发成功：归还租约回 queued；调用方随后会把案件置为 failed 并提示用户。
        try:
            release_lease(db, case_id, lease_token)
        except Exception:  # noqa: BLE001
            logger.exception("failed to release lease for case %s", case_id)
    return result


def start_recovery_sweeper(
    *, interval_seconds: float = RECOVERY_SWEEP_INTERVAL_SECONDS,
) -> bool:
    """启动当前进程唯一的 lease sweeper；返回是否实际启动。"""
    global _sweeper_stop, _sweeper_thread
    if interval_seconds <= 0:
        raise ValueError("recovery sweep interval must be positive")
    with _sweeper_lock:
        if _sweeper_thread is not None and _sweeper_thread.is_alive():
            return False
        stop = threading.Event()
        thread = threading.Thread(
            target=_recovery_sweep_loop,
            args=(stop, interval_seconds),
            name="case-lease-sweeper",
            daemon=True,
        )
        _sweeper_stop = stop
        _sweeper_thread = thread
        thread.start()
        return True



def stop_recovery_sweeper(*, timeout_seconds: float = 5.0) -> None:
    """停止常驻 sweeper；供应用优雅停机和测试清理。"""
    global _sweeper_stop, _sweeper_thread
    with _sweeper_lock:
        stop = _sweeper_stop
        thread = _sweeper_thread
    if stop is not None:
        stop.set()
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=max(0.0, timeout_seconds))
    with _sweeper_lock:
        if _sweeper_thread is thread and (thread is None or not thread.is_alive()):
            _sweeper_stop = None
            _sweeper_thread = None



def _recovery_sweep_loop(stop: threading.Event, interval_seconds: float) -> None:
    while not stop.wait(interval_seconds):
        try:
            result = recover_cases()
            if result["recovered"] or result.get("deferred"):
                logger.info("case recovery sweep: %s", result)
        except Exception:  # noqa: BLE001
            logger.exception("case recovery sweep failed")



def recover_cases() -> dict[str, int]:
    """扫描并恢复可认领案件（供启动恢复和常驻 sweeper 复用）。

    - queued：重新参与认领（租约从未建立或已随上一进程消亡）。
    - running：本进程启动意味着旧 worker 已死；仅当租约已过期/为空时同代恢复
      （resume_from_stage 由认领逻辑按 last_completed_stage 推导），租约仍新鲜
      的 running 案件可能属于其他存活进程，保持不动。
    """
    from app.db.session import SessionLocal
    from app.models import Case

    db = SessionLocal()
    recovered = deferred = 0
    try:
        available = max(0, _QUEUE_CAPACITY - running_count())
        if available == 0:
            return {"recovered": 0, "interrupted": 0, "deferred": 0}
        now = utcnow()
        stale_ids = [
            row[0]
            for row in db.execute(
                select(Case.id)
                .where(
                    or_(
                        Case.status == "queued",
                        and_(
                            Case.status == "running",
                            or_(
                                Case.lease_token == "",
                                Case.lease_expires_at.is_(None),
                                Case.lease_expires_at <= now,
                            ),
                        ),
                    )
                )
                .limit(available)
            ).all()
        ]
        for case_id in stale_ids:
            claimed = claim_lease(db, case_id, lease_owner_id())
            if claimed is None:
                # running 且租约仍新鲜：其他进程可能仍在执行，不干预。
                continue
            lease_token, generation, attempt = claimed
            result = _spawn(case_id, generation, lease_token, lease_owner_id(), attempt)
            if result == "submitted":
                recovered += 1
            elif result in ("queue_full", "submit_failed"):
                # 本进程队列满并不表示案件本身失败。归还租约保持 queued，下一轮
                # sweeper 会再次尝试；否则一次扫描就可能把积压案件批量误标失败。
                release_lease(db, case_id, lease_token)
                deferred += 1
        return {"recovered": recovered, "interrupted": 0, "deferred": deferred}
    finally:
        db.close()
