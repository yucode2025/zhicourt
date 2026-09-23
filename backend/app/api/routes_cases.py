"""案件相关 API 路由。"""
from __future__ import annotations

import asyncio
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.agents import challenger, planner
from app.core.config import settings
from app.core.logging import get_logger
from app.core.url_guard import public_source_url
from app.db.session import get_db
from app.models import (
    Argument,
    Case,
    Evidence,
    Source,
    UserQuestion,
    Verdict,
    gen_id,
)
from app.api.access_policy import require_usage_access
from app.api.routes_auth import get_anonymous_user, get_current_user, get_or_create_anon, require_user
from app.models import User
from app.schemas.case import (
    CaseDetail,
    CaseSummary,
    ChallengeRequest,
    CreateCaseRequest,
    PlanOut,
    PlanRequest,
    UserQuestionOut,
)
from app.services import api_usage, audit as audit_service
from app.services.case_lifecycle import delete_case, prepare_retry, start_generation
from app.services.cache import cache_get, cache_set, make_key
from app.services.llm.gateway import llm_case_scope, llm_error_code_is_retryable
from app.services.system_settings import get_flag, get_setting
from app.workflows.manager import submit_case
from app.services.zhihu.hackathon import HackathonContentError, list_hackathon_content

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["cases"], dependencies=[Depends(require_usage_access)])
share_router = APIRouter(prefix="/api", tags=["share"])


# ---------- 匿名会话 ----------
def get_or_create_user(request: Request, response: Response, db: Session) -> User:
    """已登录用户直接复用；否则走匿名会话。"""
    current = get_current_user(request, db)
    if current is not None:
        return current
    return get_or_create_anon(request, response, db)


def _case_public_out(case: Case) -> dict:
    """只读分享输出（不含用户私人信息）。"""
    return {"public_id": case.public_id, "case_id": case.id}


def get_case_or_404(db: Session, case_id: str) -> Case:
    case = db.execute(
        select(Case)
        .where(Case.id == case_id)
        .options(
            selectinload(Case.sources), selectinload(Case.evidence),
            selectinload(Case.claims), selectinload(Case.arguments), selectinload(Case.agent_runs),
            selectinload(Case.cross_examinations), selectinload(Case.user_questions), selectinload(Case.verdict),
        )
    ).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="案件不存在")
    return case


def check_case_view_permission(case: Case, user: User | None) -> None:
    """公开案件所有人可看；私有案件仅属主或管理员（is_demo 不改变可见性）。"""
    if case.is_public:
        return
    if user is None:
        raise HTTPException(status_code=404, detail="案件不存在")  # 不泄露私有案件存在性
    if user.id != case.user_id and user.role != "admin":
        raise HTTPException(status_code=404, detail="案件不存在")


def check_case_manage_permission(case: Case, user: User | None) -> None:
    """普通写接口仅供属主；管理员重试必须走带审计的 admin 接口。"""
    if user is None:
        raise HTTPException(status_code=404, detail="案件不存在")
    if user.id != case.user_id:
        raise HTTPException(status_code=404, detail="案件不存在")


def _request_user(request: Request, db: Session) -> User | None:
    """只解析已有正式或匿名身份，不为只读请求创建数据库记录。"""
    return get_current_user(request, db) or get_anonymous_user(request, db)


def _audit_private_admin_view(db: Session, request: Request, case: Case, user: User | None) -> None:
    if not case.is_public and user is not None and user.role == "admin" and user.id != case.user_id:
        # 事件轮询和子资源并行加载会反复命中读接口；同一管理员/案件五分钟记一条，
        # 保留敏感访问证据，同时避免审计表被轮询请求淹没。
        audit_key = "audit:private-view:" + make_key(user.id, case.id)
        if cache_get(audit_key) is not None:
            return
        audit_service.audit(db, request, user.id, "CASE_VIEWED_PRIVATE", case.id, case.title[:80])
        cache_set(audit_key, True, ttl_seconds=5 * 60)


def get_viewable_case_or_404(db: Session, case_id: str, user: User | None, request: Request) -> Case:
    """案件子资源统一入口：存在 + 可见，缺一不可。"""
    case = get_case_or_404(db, case_id)
    check_case_view_permission(case, user)
    _audit_private_admin_view(db, request, case, user)
    return case


@router.post("/plan", response_model=PlanOut)
async def create_plan(req: PlanRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    if api_usage.rate_limit_hit(f"plan:{request.client.host if request.client else 'unknown'}", 30, 3600):
        raise HTTPException(status_code=429, detail="操作过于频繁，请稍后再试")
    if len(req.question) > get_setting(db, "case_input_max_length"):
        raise HTTPException(status_code=422, detail="问题长度超过管理员设置的限制")
    get_or_create_user(request, response, db)
    with llm_case_scope(fail_on_error=True):
        plan = await planner.run_planner(req.question)
    return PlanOut(**plan.to_dict())


# ---------- 案件 CRUD ----------
@router.post("/cases", response_model=CaseSummary, status_code=201)
async def create_case(req: CreateCaseRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    if api_usage.rate_limit_hit(f"create:{request.client.host if request.client else 'unknown'}", 10, 3600):
        raise HTTPException(status_code=429, detail="创建案件过于频繁（每小时最多 10 个），请稍后再试")
    user = get_or_create_user(request, response, db)
    # 游客创建案件受 Feature Flag 控制；登录用户不受限
    if user.account_kind == "anonymous" and not get_flag(db, "allow_guest_cases"):
        raise HTTPException(status_code=403, detail="当前仅注册用户可创建案件，请先登录")
    max_len = int(get_setting(db, "case_input_max_length"))
    if len(req.question) > max_len:
        raise HTTPException(status_code=422, detail=f"问题长度超过限制（{max_len} 字）")
    kind, reason = planner.classify_input(req.question)
    if kind != "debatable":
        raise HTTPException(status_code=422, detail=reason)
    if req.title is not None and not req.title:
        raise HTTPException(status_code=422, detail="标题不能为空")
    case = Case(
        id=gen_id("case"),
        user_id=user.id,
        title=(req.title if req.title is not None else req.question)[:200],
        original_question=req.question,
        proposition=req.proposition or req.question,
        status="created",
        is_public=False,
        public_id=None,
    )
    db.add(case)
    db.commit()
    return case


# ---------- 公开分享（只读，按 public_id） ----------
@share_router.get("/share/{public_id}")
async def get_shared_case(public_id: str, request: Request, db: Session = Depends(get_db)):
    from app.models import User as U

    case = db.execute(
        select(Case)
        .where(Case.public_id == public_id)
        .options(
            selectinload(Case.sources), selectinload(Case.evidence),
            selectinload(Case.arguments), selectinload(Case.cross_examinations),
            selectinload(Case.verdict),
        )
    ).scalar_one_or_none()
    if case is None or not case.is_public:
        raise HTTPException(status_code=404, detail="分享内容不存在")
    # 只读输出：可追溯结构 + 判决，不含 owner 信息、不含 user_questions
    from app.schemas.case import ArgumentOut, CrossExamOut, EvidenceOut, SourceOut, VerdictOut

    execution = CaseSummary.model_validate(case)
    return {
        "title": case.title,
        "proposition": case.proposition,
        "status": case.status,
        "is_demo": case.is_demo,
        "engine_mode": execution.engine_mode,
        "source_mode": execution.source_mode,
        "execution_summary": execution.execution_summary,
        "created_at": case.created_at.isoformat() + "Z" if case.created_at else None,
        "sources": [SourceOut.model_validate(s).model_dump() for s in case.sources],
        "evidence": [EvidenceOut.model_validate(e).model_dump() for e in case.evidence],
        "arguments": [ArgumentOut.model_validate(a).model_dump() for a in case.arguments],
        "cross_examinations": [CrossExamOut.model_validate(c).model_dump() for c in case.cross_examinations],
        "verdict": VerdictOut.model_validate(case.verdict).model_dump() if case.verdict else None,
    }


def _submit_or_fail(db: Session, case: Case) -> None:
    result = submit_case(db, case.id)
    if result in ("submitted", "already_active"):
        return
    case.status = "failed"
    case.current_stage = "queue"
    case.failure_stage = "queue"
    case.failure_kind = "queue_unavailable"
    case.failure_code = "queue_full" if result == "queue_full" else "submit_failed"
    case.error_message = "审理队列已满，请稍后重新审理。" if result == "queue_full" else "审理任务提交失败，请稍后重试。"
    db.commit()
    raise HTTPException(status_code=503, detail=case.error_message)


@router.post("/cases/{case_id}/start", response_model=CaseSummary)
async def start_case(case_id: str, request: Request, db: Session = Depends(get_db)):
    user = _request_user(request, db)
    case = get_case_or_404(db, case_id)
    check_case_manage_permission(case, user)
    if case.status == "verdict_ready":
        return case
    if case.status == "failed":
        raise HTTPException(status_code=409, detail="失败案件请使用重新审理")
    if case.status == "created":
        kind, reason = planner.classify_input(case.original_question)
        if kind != "debatable":
            raise HTTPException(status_code=422, detail=reason)
        start_generation(db, case)
        db.commit()
    # queued / running：由 DB 权威的条件认领决定；租约有效 → already_active。
    _submit_or_fail(db, case)
    db.expire_all()
    return db.get(Case, case.id)


@router.post("/cases/{case_id}/retry", response_model=CaseSummary)
async def retry_case(case_id: str, request: Request, db: Session = Depends(get_db)):
    user = _request_user(request, db)
    case = get_case_or_404(db, case_id)
    check_case_manage_permission(case, user)
    try:
        prepare_retry(db, case)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    _submit_or_fail(db, case)
    db.expire_all()
    return db.get(Case, case.id)


@router.get("/cases", response_model=list[CaseSummary])
async def list_cases(
    request: Request,
    q: str = Query(default="", max_length=100),
    status: str = Query(default="", max_length=20),
    limit: int = Query(default=50, le=100),
    db: Session = Depends(get_db),
):
    user = _request_user(request, db)
    # 可见性：自己的案件 + 公开案件；未识别身份只能看到公开案件。
    visibility = ((Case.user_id == user.id) | Case.is_public) if user is not None else Case.is_public
    stmt = select(Case).where(visibility).order_by(desc(Case.created_at)).limit(limit)
    if q:
        stmt = stmt.where(Case.title.contains(q) | Case.proposition.contains(q))
    if status:
        stmt = stmt.where(Case.status == status)
    return list(db.execute(stmt).scalars().all())


@router.post("/cases/{case_id}/publish", response_model=CaseSummary)
async def publish_case(case_id: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    case = get_case_or_404(db, case_id)
    if user is None or case.user_id != user.id:
        raise HTTPException(status_code=404, detail="案件不存在")
    if case.status != "verdict_ready" or case.verdict is None:
        raise HTTPException(status_code=409, detail="案件完成并生成判决书后才能公开")
    if not case.is_public:
        case.is_public = True
        case.public_id = secrets.token_urlsafe(18)
        db.commit()
    return case


@router.post("/cases/{case_id}/unpublish", response_model=CaseSummary)
async def unpublish_case(case_id: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    case = get_case_or_404(db, case_id)
    if user is None or case.user_id != user.id:
        raise HTTPException(status_code=404, detail="案件不存在")
    case.is_public = False
    case.public_id = None
    db.commit()
    return case


@router.delete("/cases/{case_id}", status_code=204)
async def remove_case(case_id: str, request: Request, db: Session = Depends(get_db)):
    user = _request_user(request, db)
    case = get_case_or_404(db, case_id)
    if user is None or case.user_id != user.id:
        raise HTTPException(status_code=404, detail="案件不存在")
    try:
        delete_case(db, case)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=204)


@router.get("/cases/{case_id}", response_model=CaseDetail)
async def get_case(case_id: str, request: Request, db: Session = Depends(get_db)):
    user = _request_user(request, db)
    case = get_case_or_404(db, case_id)
    check_case_view_permission(case, user)
    _audit_private_admin_view(db, request, case, user)
    out = CaseDetail.model_validate(case)
    out.is_owner = user is not None and case.user_id == user.id
    out.user_questions = [
        UserQuestionOut.model_validate(q)
        for q in case.user_questions
        if user is not None and q.user_id == user.id
    ]
    return out


@router.get("/cases/{case_id}/sources")
async def case_sources(case_id: str, request: Request, db: Session = Depends(get_db)):
    get_viewable_case_or_404(db, case_id, _request_user(request, db), request)
    from app.schemas.case import SourceOut

    return [SourceOut.model_validate(source) for source in db.execute(select(Source).where(Source.case_id == case_id).order_by(desc(Source.rank_score))).scalars().all()]


@router.get("/cases/{case_id}/evidence")
async def case_evidence(case_id: str, request: Request, db: Session = Depends(get_db)):
    get_viewable_case_or_404(db, case_id, _request_user(request, db), request)
    return list(db.execute(select(Evidence).where(Evidence.case_id == case_id)).scalars().all())


@router.get("/cases/{case_id}/verdict")
async def case_verdict(case_id: str, request: Request, db: Session = Depends(get_db)):
    get_viewable_case_or_404(db, case_id, _request_user(request, db), request)
    verdict = db.execute(select(Verdict).where(Verdict.case_id == case_id)).scalar_one_or_none()
    if verdict is None:
        raise HTTPException(status_code=404, detail="判决书尚未生成")
    return verdict


# ---------- SSE 实时进度 ----------
def _case_failure_payload(case: Case) -> dict | None:
    if case.status != "failed":
        return None
    code = case.failure_code or "case_failed"
    return {
        "code": code,
        "message": case.error_message or "案件审理失败，请稍后重新审理。",
        "stage": case.failure_stage or case.current_stage,
        "retryable": (
            llm_error_code_is_retryable(code)
            if case.failure_kind == "llm_upstream"
            else case.failure_kind in {"network", "timeout", "queue_unavailable", "interrupted"}
            or code in {"queue_full", "submit_failed"}
        ),
    }




@router.get("/cases/{case_id}/events")
async def case_events(case_id: str, request: Request, after: int | None = Query(default=None), db: Session = Depends(get_db)):
    """两种模式（after 显式判定）：
    - `after` 参数显式提供时（含 -1）：返回 index > after 的事件（增量轮询 JSON），
      并携带 generation；`after=-1` 表示从头拉取全部事件。
    - 未提供 `after`：SSE 流。SSE 事件 id 为 `generation:index`，done 携带 generation，
      断线重连通过 Last-Event-ID 恢复到 `generation:index` 之后。
    """
    case = get_viewable_case_or_404(db, case_id, _request_user(request, db), request)
    if after is not None:
        events = case.progress_events or []
        return {
            "events": [e for e in events if e["i"] > after],
            "status": case.status,
            "index": case.progress_index,
            "generation": case.run_generation or 0,
            "error": _case_failure_payload(case),
        }

    from fastapi.responses import StreamingResponse

    last_index = -1
    last_event_id = request.headers.get("last-event-id", "")
    if last_event_id:
        # id 形如 generation:index；旧客户端只发 index 时也兼容
        try:
            _, raw_index = last_event_id.rsplit(":", 1)
            last_index = int(raw_index)
        except ValueError:
            last_index = -1

    async def event_stream():
        stream_index = last_index
        idle = 0.0
        while idle < 3600:  # 最长 1 小时
            if await request.is_disconnected():
                return
            # 每轮新会话，避免长事务
            from app.db.session import SessionLocal

            s = SessionLocal()
            try:
                c = s.get(Case, case_id)
                if c is None:
                    return
                generation = c.run_generation or 0
                events = c.progress_events or []
                new = [e for e in events if e["i"] > stream_index]
                for e in new:
                    yield f"id: {generation}:{e['i']}\nevent: progress\ndata: {_safe_json(e)}\n\n"
                    stream_index = e["i"]
                if c.status in ("verdict_ready", "failed"):
                    yield (
                        "event: done\ndata: "
                        + _safe_json({
                            "status": c.status, "generation": generation,
                            "error": _case_failure_payload(c),
                        })
                        + "\n\n"
                    )
                    return
            finally:
                s.close()
            await asyncio.sleep(0.8)
            idle += 0.8

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


def _safe_json(e: dict) -> str:
    import json

    return json.dumps(e, ensure_ascii=False)


# ---------- 收藏（判决书所在案件） ----------
@router.post("/cases/{case_id}/favorite", status_code=201)
async def add_favorite(case_id: str, request: Request, response: Response, db: Session = Depends(get_db)):
    from app.models import Favorite

    user = get_current_user(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录后收藏")
    # 只能收藏自己可见的案件
    case = get_case_or_404(db, case_id)
    check_case_view_permission(case, user)
    _audit_private_admin_view(db, request, case, user)
    exists = db.query(Favorite).filter(Favorite.user_id == user.id, Favorite.case_id == case_id).first()
    if exists is None:
        db.add(Favorite(id=gen_id("fav"), user_id=user.id, case_id=case_id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            exists = db.query(Favorite).filter(Favorite.user_id == user.id, Favorite.case_id == case_id).first()
            if exists is None:
                raise
    return {"ok": True, "is_favorite": True}


@router.delete("/cases/{case_id}/favorite")
async def remove_favorite(case_id: str, request: Request, db: Session = Depends(get_db)):
    from app.models import Favorite

    user = get_current_user(request, db)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    # 不可见案件的存在性同样不泄露
    case = get_case_or_404(db, case_id)
    check_case_view_permission(case, user)
    _audit_private_admin_view(db, request, case, user)
    db.query(Favorite).filter(Favorite.user_id == user.id, Favorite.case_id == case_id).delete()
    db.commit()
    return {"ok": True, "is_favorite": False}


@router.get("/cases/{case_id}/favorite")
async def favorite_status(case_id: str, request: Request, db: Session = Depends(get_db)):
    from app.models import Favorite

    user = get_current_user(request, db)
    # 状态查询也不泄露私有案件存在性
    case = get_case_or_404(db, case_id)
    check_case_view_permission(case, user)
    _audit_private_admin_view(db, request, case, user)
    if user is None:
        return {"is_favorite": False}
    exists = db.query(Favorite).filter(Favorite.user_id == user.id, Favorite.case_id == case_id).first()
    return {"is_favorite": exists is not None}


# ---------- 用户质询 ----------
_CHALLENGE_EVIDENCE_LIMIT = 20


def _challenge_focus(case: Case, target: str, ref_id: str | None) -> tuple[str, list[Evidence]]:
    """在调用模型前解析且验证本案引用；不允许用猜测的对象替代焦点。"""
    evidence_items = list(case.evidence)
    evidence = {item.id: item for item in evidence_items}
    ref_id = ref_id.strip() if ref_id is not None else None
    if ref_id == "":
        raise HTTPException(status_code=422, detail="质询对象编号不能为空")

    def focused(ids: set[str]) -> list[Evidence]:
        return [item for item in evidence_items if item.id in ids][:_CHALLENGE_EVIDENCE_LIMIT]

    if target == "evidence":
        item = evidence.get(ref_id)
        if item is None:
            raise HTTPException(status_code=422, detail="证据必须引用本案证据")
        return f"证据 {item.id}: {item.claim[:200]}", [item]
    if target == "source":
        source = next((item for item in case.sources if item.id == ref_id), None)
        if source is None:
            raise HTTPException(status_code=422, detail="来源必须引用本案来源")
        items = [item for item in evidence_items if item.source_id == source.id][:_CHALLENGE_EVIDENCE_LIMIT]
        return f"来源 {source.id}: {source.title[:160]}；摘要：{source.summary[:240]}", items
    if target in ("prosecution", "defense"):
        arguments = [item for item in case.arguments if item.side == target]
        if ref_id is not None:
            arguments = [item for item in arguments if item.id == ref_id]
            if not arguments:
                raise HTTPException(status_code=422, detail="论证必须引用本案对应阵营论证")
        ids = {evidence_id for argument in arguments for evidence_id in argument.evidence_ids}
        detail = "；".join(
            f"{argument.id} {argument.title[:100]}: {argument.body[:240]}"
            for argument in arguments[:6]
        )
        focus = f"{target} {'单条论证' if ref_id else '整方论证'}：{detail}"
        return focus[:1500], focused(ids)
    if target == "judge":
        if ref_id is not None and (case.verdict is None or case.verdict.id != ref_id):
            raise HTTPException(status_code=422, detail="判决必须引用本案判决")
        verdict = case.verdict
        ids = set(
            (verdict.strongest_evidence_ids or [])
            + (verdict.strongest_counter_evidence_ids or [])
        ) if verdict else set()
        focus = f"Judge 判决：{verdict.conclusion if verdict else '暂无判决'}"
        return focus[:1500], focused(ids)
    raise HTTPException(status_code=422, detail="未知质询对象")


@router.post("/cases/{case_id}/questions", response_model=UserQuestionOut, status_code=201)
async def ask_question(
    case_id: str, req: ChallengeRequest, request: Request, response: Response, db: Session = Depends(get_db)
):
    if len(req.text) > get_setting(db, "challenge_input_max_length"):
        raise HTTPException(status_code=422, detail="质询长度超过管理员设置的限制")
    # target_ref_id 契约失败必须在创建匿名身份、计入限流或写入质询前返回。
    user = _request_user(request, db)
    case = get_case_or_404(db, case_id)
    check_case_view_permission(case, user)
    # 管理员对私有案件只有受审计的排障读取权，不能通过普通接口注入质询内容。
    if not case.is_public and (user is None or case.user_id != user.id):
        raise HTTPException(status_code=404, detail="案件不存在")
    focus, evidence = _challenge_focus(case, req.target, req.target_ref_id)
    if case.status != "verdict_ready":
        raise HTTPException(status_code=409, detail="案件审理尚未完成，无法质询")
    if api_usage.rate_limit_hit(f"question:{request.client.host if request.client else 'unknown'}", 20, 3600):
        raise HTTPException(status_code=429, detail="质询过于频繁，请稍后再试")

    user = get_or_create_user(request, response, db)
    judge_mode = next((run.mode for run in case.agent_runs if run.agent == "judge"), "heuristic")
    with llm_case_scope(fail_on_error=True):
        result, mode = await challenger.run_challenger(
            case.proposition, req.target, req.text, evidence,
            mode="llm" if judge_mode == "llm" and not case.is_demo else "heuristic", focus=focus,
        )
    uq = UserQuestion(
        id=gen_id("uq"), case_id=case.id, user_id=user.id, target=req.target,
        target_ref_id=req.target_ref_id, text=req.text,
        challenge_type=result["challenge_type"], response=result["response"],
        related_evidence_ids=result.get("related_evidence_ids", []),
    )
    db.add(uq)
    db.commit()
    return uq


# ---------- 热榜 ----------
@router.get("/hot")
async def hot_list(limit: int = Query(default=10, le=20), db: Session = Depends(get_db)):
    """知乎热榜：缓存 25 分钟。受 Feature Flag enable_hot_cases 控制。"""
    if not get_flag(db, "enable_hot_cases"):
        return {"items": [], "is_demo": False, "error": "热榜功能已由管理员关闭"}
    from app.services.zhihu.search import get_zhihu_provider

    try:
        provider = get_zhihu_provider(db)
        cache_key = "zhihu:hot:v2:" + ("demo" if provider.is_demo else make_key(provider.base_url, provider.api_key, provider.paths.get("zhihu_hot_path")))
        cached = cache_get(cache_key)
        if cached is not None:
            api_usage.record("zhihu_hot", "hot", cache_hit=True)
            return {"items": [item | {"url": public_source_url(item.get("url", ""))} for item in cached[:limit]], "is_demo": provider.is_demo}
        items = await provider.hot_list(20)
    except Exception as exc:  # noqa: BLE001
        logger.warning("hot list failed: %s", exc)
        return {"items": [], "is_demo": False, "error": "热榜暂时不可用"}
    data = [i.model_dump() | {"url": public_source_url(i.url), "is_demo": provider.is_demo} for i in items]
    cache_set(cache_key, data, ttl_seconds=25 * 60)
    return {"items": data[:limit], "is_demo": provider.is_demo}


# ---------- 黑客松活动内容（无需 Access Secret） ----------
@router.get("/hackathon/content")
async def hackathon_content_list(
    request: Request,
    kind: Literal["knowledge", "story"] = "knowledge",
    limit: int = Query(default=12, ge=1, le=20),
):
    """官方活动故事/知识列表；仅作选题入口，不自动当作庭审证据。"""
    ip = request.client.host if request.client else "unknown"
    if api_usage.rate_limit_hit(f"hackathon-content:{ip}", 120, 3600):
        raise HTTPException(status_code=429, detail="活动内容请求过于频繁，请稍后再试")
    try:
        items = await list_hackathon_content(kind, limit)
    except HackathonContentError as exc:
        logger.warning("hackathon content list failed: %s", exc)
        return {"kind": kind, "items": [], "error": str(exc)}
    return {"kind": kind, "items": items}


# ---------- 用量 ----------
@router.get("/usage")
async def usage():
    return {"providers": api_usage.snapshot()}
