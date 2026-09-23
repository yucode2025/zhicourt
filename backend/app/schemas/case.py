"""Pydantic API Schema（统一 Error Schema + 输出模型）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.url_guard import public_source_url
from app.services.execution_mode import derive_engine_mode


# ---------- 统一错误 ----------
class ErrorBody(BaseModel):
    code: str
    message: str
    detail: str | None = None


class ErrorResponse(BaseModel):
    ok: bool = False
    error: ErrorBody


# ---------- 请求 ----------
class PlanRequest(BaseModel):
    question: str = Field(min_length=4, max_length=5000)

    @field_validator("question")
    @classmethod
    def strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("问题不能为空")
        return v


class CreateCaseRequest(BaseModel):
    question: str = Field(min_length=4, max_length=5000)
    proposition: str | None = Field(default=None, max_length=300)
    title: str | None = Field(default=None, max_length=200)

    @field_validator("question", "title")
    @classmethod
    def strip_input(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class ChallengeRequest(BaseModel):
    target: Literal["prosecution", "defense", "judge", "evidence", "source"]
    target_ref_id: str | None = Field(default=None, max_length=40)
    text: str = Field(min_length=4, max_length=5000)

    @field_validator("target_ref_id")
    @classmethod
    def strip_ref(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("text")
    @classmethod
    def strip_challenge(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 4:
            raise ValueError("质询内容至少需要 4 个字")
        return value


# ---------- 响应 ----------
class SourceOut(BaseModel):
    id: str
    origin: str
    kind: str
    title: str
    url: str
    author: str
    summary: str
    published_at: str
    vote_count: int
    comment_count: int
    rank_score: float
    independence_score: float
    authority_level: int | None = None
    is_demo: bool

    @field_validator("url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        return public_source_url(value)

    class Config:
        from_attributes = True


class EvidenceOut(BaseModel):
    id: str
    source_id: str
    claim: str
    stance: str
    evidence_type: str
    summary: str
    quoted_fragment: str | None
    strength: float
    limitations: list

    class Config:
        from_attributes = True


class ClaimOut(BaseModel):
    id: str
    side: str
    text: str
    evidence_ids: list

    class Config:
        from_attributes = True


class ArgumentOut(BaseModel):
    id: str
    side: str
    title: str
    body: str
    evidence_ids: list
    claim_ids: list
    strength: float

    class Config:
        from_attributes = True


class AgentRunOut(BaseModel):
    id: str
    agent: str
    status: str
    mode: str
    output_summary: str
    error: str | None
    duration_ms: int

    class Config:
        from_attributes = True


class CrossExamOut(BaseModel):
    id: str
    target_side: str
    issue_type: str
    severity: str
    description: str
    related_evidence_ids: list

    class Config:
        from_attributes = True


class UserQuestionOut(BaseModel):
    id: str
    target: str
    target_ref_id: str | None
    text: str
    challenge_type: str
    response: str
    related_evidence_ids: list
    created_at: datetime

    class Config:
        from_attributes = True


class VerdictOut(BaseModel):
    id: str
    conclusion: str
    conclusion_stance: str
    confidence: float
    prosecution_summary: str
    defense_summary: str
    shared_facts: list
    core_disputes: list
    strongest_evidence_ids: list
    strongest_counter_evidence_ids: list
    evidence_gaps: list
    definition_conflicts: list
    unknowns: list
    verdict_changers: list
    next_questions: list
    cross_exam_summary: str

    class Config:
        from_attributes = True


class CaseSummary(BaseModel):
    id: str
    title: str
    proposition: str
    status: str
    current_stage: str
    engine_mode: str
    source_mode: str = "none"
    execution_summary: dict[str, Any] = Field(default_factory=dict)
    is_demo: bool
    is_public: bool = False
    public_id: str | None = None
    progress_index: int
    created_at: datetime
    error_message: str | None = None
    failure_stage: str = ""
    failure_kind: str = ""
    failure_code: str = ""

    @model_validator(mode="before")
    @classmethod
    def derive_execution(cls, value):
        if not hasattr(value, "agent_runs") or isinstance(value, dict):
            return value
        runs = list(value.agent_runs)
        # engine_mode 口径全系统唯一：services.execution_mode（排除 verdict_writer/provider/failed）
        derived_engine = derive_engine_mode(runs)
        engine = derived_engine or value.engine_mode
        origins = {"mock" if source.is_demo else "real" for source in value.sources}
        source_mode = "mixed" if len(origins) > 1 else next(iter(origins), "none")
        return {
            **{field: getattr(value, field) for field in (
                "id", "title", "proposition", "status", "current_stage", "is_demo", "is_public",
                "public_id", "progress_index", "created_at", "error_message",
                "failure_stage", "failure_kind", "failure_code",
            )},
            "engine_mode": engine, "source_mode": source_mode,
            "execution_summary": {"engine_resolved": derived_engine is not None,
                                  "agent_modes": {run.agent: run.mode for run in runs},
                                  "real_sources": sum(not s.is_demo for s in value.sources),
                                  "demo_sources": sum(s.is_demo for s in value.sources)},
            **({field: getattr(value, field) for field in (
                "original_question", "plan", "progress_events", "sources", "evidence", "claims",
                "arguments", "agent_runs", "cross_examinations", "user_questions", "verdict",
            )} if cls.__name__ == "CaseDetail" else {}),
        }

    class Config:
        from_attributes = True


class CaseDetail(CaseSummary):
    original_question: str
    plan: dict | None
    progress_events: list
    is_owner: bool = False
    sources: list[SourceOut]
    evidence: list[EvidenceOut]
    claims: list[ClaimOut]
    arguments: list[ArgumentOut]
    agent_runs: list[AgentRunOut]
    cross_examinations: list[CrossExamOut]
    user_questions: list[UserQuestionOut]
    verdict: VerdictOut | None

    class Config:
        from_attributes = True


class PlanOut(BaseModel):
    title: str
    proposition: str
    key_concepts: list[str]
    disputes: list[str]
    sub_questions: list[str]
    search_queries: list[str]
    suitable: bool
    needs_rewrite: bool
    mode: str
    input_classification: str = "debatable"
    rejection_reason: str | None = None


class HotItemOut(BaseModel):
    title: str
    url: str
    heat: int
    excerpt: str
    is_demo: bool


class UsageOut(BaseModel):
    providers: dict[str, Any]
