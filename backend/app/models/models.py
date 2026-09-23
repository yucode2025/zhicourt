"""SQLAlchemy 数据模型（MariaDB / SQLite）。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


JSONList = MutableList.as_mutable(JSON)


class Base(DeclarativeBase):
    pass


class User(Base):
    """用户：匿名会话用户（username 为空）与正式用户共用一张表。"""
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('user','admin')", name="ck_users_role"),
        CheckConstraint("status IN ('active','disabled')", name="ck_users_status"),
        CheckConstraint("account_kind IN ('member','anonymous','zhihu')", name="ck_users_account_kind"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    username: Mapped[str | None] = mapped_column(String(50), unique=True, index=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    nickname: Mapped[str | None] = mapped_column(String(50), nullable=True)
    avatar: Mapped[str | None] = mapped_column(String(20), nullable=True)
    role: Mapped[str] = mapped_column(String(15), default="user", index=True)
    status: Mapped[str] = mapped_column(String(15), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    account_kind: Mapped[str] = mapped_column(String(15), default="member", index=True)

    cases: Mapped[list["Case"]] = relationship(back_populates="user")
    favorites: Mapped[list["Favorite"]] = relationship(back_populates="user")


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[str] = mapped_column(String(60), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ip: Mapped[str] = mapped_column(String(45), default="")


class AnonymousSession(Base):
    __tablename__ = "anonymous_sessions"
    __table_args__ = (CheckConstraint("status IN ('active','migrated','revoked','expired')", name="ck_anonymous_sessions_status"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(15), default="active", index=True)
    migrated_to_user_id: Mapped[str | None] = mapped_column(String(40), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    ip: Mapped[str] = mapped_column(String(45), default="")


class ZhihuOAuthState(Base):
    """知乎 OAuth 一次性 state：只存哈希及发起上下文，不存 code/token。"""
    __tablename__ = "zhihu_oauth_states"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256(state)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    ip: Mapped[str] = mapped_column(String(45), default="")
    purpose: Mapped[str] = mapped_column(String(10), default="login")
    initiating_user_id: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    initiating_session_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    anonymous_session_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ZhihuOAuthAccount(Base):
    """知乎授权身份与应用用户的绑定；遗留 Token 列保持为空。"""
    __tablename__ = "zhihu_oauth_accounts"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    zhihu_uid: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    zhihu_hash_id: Mapped[str] = mapped_column(String(80), default="")
    zhihu_nickname: Mapped[str | None] = mapped_column(String(120), nullable=True)
    zhihu_avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # 保留列只为平滑升级；登录所获 Token 不持久化，迁移会清空历史值。
    access_token: Mapped[str] = mapped_column(String(2048), default="")
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Favorite(Base):
    __tablename__ = "favorites"
    __table_args__ = (UniqueConstraint("user_id", "case_id", name="uq_favorites_user_case"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(40), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[str] = mapped_column(String(40), ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    user: Mapped[User] = relationship(back_populates="favorites")
    case: Mapped["Case"] = relationship()


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"
    __table_args__ = (CheckConstraint("result IN ('ok','denied','failed')", name="ck_admin_audit_logs_result"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    admin_user_id: Mapped[str] = mapped_column(String(40), index=True)
    action: Mapped[str] = mapped_column(String(50))
    target: Mapped[str] = mapped_column(String(120), default="")
    detail: Mapped[str] = mapped_column(String(500), default="")
    ip: Mapped[str] = mapped_column(String(45), default="")
    result: Mapped[str] = mapped_column(String(10), default="ok")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    updated_by: Mapped[str] = mapped_column(String(40), default="")


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (
        CheckConstraint("status IN ('created','queued','running','verdict_ready','failed')", name="ck_cases_status"),
        CheckConstraint("engine_mode IN ('heuristic','llm','mixed')", name="ck_cases_engine_mode"),
        CheckConstraint("progress_index >= 0", name="ck_cases_progress_index"),
        CheckConstraint("run_generation >= 0", name="ck_cases_run_generation"),
        CheckConstraint("retry_count >= 0", name="ck_cases_retry_count"),
        Index("ix_cases_status_generation", "status", "run_generation"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(40), ForeignKey("users.id", ondelete="SET NULL"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    original_question: Mapped[str] = mapped_column(Text)
    proposition: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="created", index=True)
    current_stage: Mapped[str] = mapped_column(String(40), default="")
    plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    progress_events: Mapped[list] = mapped_column(JSONList, default=list)
    progress_index: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    engine_mode: Mapped[str] = mapped_column(String(20), default="heuristic")
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    public_id: Mapped[str | None] = mapped_column(String(30), unique=True, index=True, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    # ---- 执行 fencing：generation 标识一次逻辑运行；lease 标识当前唯一持有者 ----
    run_generation: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str] = mapped_column(String(60), default="")
    lease_owner: Mapped[str] = mapped_column(String(80), default="")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lease_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    run_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resume_from_stage: Mapped[str] = mapped_column(String(40), default="")
    last_completed_stage: Mapped[str] = mapped_column(String(40), default="")
    failure_stage: Mapped[str] = mapped_column(String(40), default="")
    failure_kind: Mapped[str] = mapped_column(String(30), default="")
    failure_code: Mapped[str] = mapped_column(String(60), default="")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped[User | None] = relationship(back_populates="cases")
    sources: Mapped[list["Source"]] = relationship(back_populates="case", cascade="all, delete-orphan", passive_deletes=True)
    evidence: Mapped[list["Evidence"]] = relationship(back_populates="case", cascade="all, delete-orphan", passive_deletes=True)
    claims: Mapped[list["Claim"]] = relationship(back_populates="case", cascade="all, delete-orphan", passive_deletes=True)
    arguments: Mapped[list["Argument"]] = relationship(back_populates="case", cascade="all, delete-orphan", passive_deletes=True)
    agent_runs: Mapped[list["AgentRun"]] = relationship(back_populates="case", cascade="all, delete-orphan", passive_deletes=True)
    cross_examinations: Mapped[list["CrossExamination"]] = relationship(back_populates="case", cascade="all, delete-orphan", passive_deletes=True)
    user_questions: Mapped[list["UserQuestion"]] = relationship(back_populates="case", cascade="all, delete-orphan", passive_deletes=True)
    verdict: Mapped["Verdict | None"] = relationship(back_populates="case", uselist=False, cascade="all, delete-orphan", passive_deletes=True)


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("id", "case_id", name="uq_sources_id_case"),
        CheckConstraint("origin IN ('zhihu','web','demo')", name="ck_sources_origin"),
        CheckConstraint("vote_count >= 0 AND comment_count >= 0", name="ck_sources_counts"),
        CheckConstraint("relevance_score BETWEEN 0 AND 1", name="ck_sources_relevance_score"),
        CheckConstraint("authority_score BETWEEN 0 AND 1", name="ck_sources_authority_score"),
        CheckConstraint("community_score BETWEEN 0 AND 1", name="ck_sources_community_score"),
        CheckConstraint("recency_score BETWEEN 0 AND 1", name="ck_sources_recency_score"),
        CheckConstraint("independence_score BETWEEN 0 AND 1", name="ck_sources_independence_score"),
        CheckConstraint("rank_score BETWEEN 0 AND 1", name="ck_sources_rank_score"),
        CheckConstraint("authority_level IS NULL OR authority_level BETWEEN 1 AND 5", name="ck_sources_authority_level"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(40), ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    origin: Mapped[str] = mapped_column(String(10))
    kind: Mapped[str] = mapped_column(String(20), default="")
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(120), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[str] = mapped_column(String(30), default="")
    vote_count: Mapped[int] = mapped_column(Integer, default=0)
    comment_count: Mapped[int] = mapped_column(Integer, default=0)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    authority_score: Mapped[float] = mapped_column(Float, default=0.0)
    community_score: Mapped[float] = mapped_column(Float, default=0.0)
    recency_score: Mapped[float] = mapped_column(Float, default=0.0)
    independence_score: Mapped[float] = mapped_column(Float, default=1.0)
    rank_score: Mapped[float] = mapped_column(Float, default=0.0)
    authority_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    case: Mapped[Case] = relationship(back_populates="sources")
    evidence_items: Mapped[list["Evidence"]] = relationship(
        back_populates="source", passive_deletes=True, overlaps="case,evidence"
    )

    @property
    def official_authority_level(self) -> int | None:
        """旧调用方兼容别名；真实持久化字段为 authority_level。"""
        return self.authority_level

    @official_authority_level.setter
    def official_authority_level(self, value: int | None) -> None:
        self.authority_level = value


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint("id", "case_id", name="uq_evidence_id_case"),
        ForeignKeyConstraint(["source_id", "case_id"], ["sources.id", "sources.case_id"], name="fk_evidence_source_case", ondelete="CASCADE"),
        CheckConstraint("stance IN ('pro','con','neutral')", name="ck_evidence_stance"),
        CheckConstraint("evidence_type IN ('fact','opinion','data','case','prediction','assumption','limitation')", name="ck_evidence_type"),
        CheckConstraint("relevance_score BETWEEN 0 AND 1", name="ck_evidence_relevance_score"),
        CheckConstraint("authority_score BETWEEN 0 AND 1", name="ck_evidence_authority_score"),
        CheckConstraint("strength BETWEEN 0 AND 1", name="ck_evidence_strength"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(40), ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[str] = mapped_column(String(40), index=True)
    claim: Mapped[str] = mapped_column(Text)
    stance: Mapped[str] = mapped_column(String(10), default="neutral")
    evidence_type: Mapped[str] = mapped_column(String(15), default="opinion")
    summary: Mapped[str] = mapped_column(Text, default="")
    quoted_fragment: Mapped[str | None] = mapped_column(Text, nullable=True)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    authority_score: Mapped[float] = mapped_column(Float, default=0.0)
    strength: Mapped[float] = mapped_column(Float, default=0.0)
    limitations: Mapped[list] = mapped_column(JSONList, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    case: Mapped[Case] = relationship(back_populates="evidence", foreign_keys=[case_id], overlaps="evidence_items,source")
    source: Mapped[Source] = relationship(
        back_populates="evidence_items", foreign_keys=[source_id, case_id], overlaps="case,evidence"
    )


class Claim(Base):
    __tablename__ = "claims"
    __table_args__ = (
        UniqueConstraint("id", "case_id", name="uq_claims_id_case"),
        CheckConstraint("side IN ('pro','con','court')", name="ck_claims_side"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(40), ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    side: Mapped[str] = mapped_column(String(10), default="court")
    text: Mapped[str] = mapped_column(Text)
    evidence_ids: Mapped[list] = mapped_column(JSONList, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    case: Mapped[Case] = relationship(back_populates="claims")


class Argument(Base):
    __tablename__ = "arguments"
    __table_args__ = (
        UniqueConstraint("id", "case_id", name="uq_arguments_id_case"),
        CheckConstraint("side IN ('prosecution','defense','court')", name="ck_arguments_side"),
        CheckConstraint("strength BETWEEN 0 AND 1", name="ck_arguments_strength"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(40), ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    side: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    evidence_ids: Mapped[list] = mapped_column(JSONList, default=list)
    claim_ids: Mapped[list] = mapped_column(JSONList, default=list)
    strength: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    case: Mapped[Case] = relationship(back_populates="arguments")


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint("status IN ('running','done','failed','skipped','reused')", name="ck_agent_runs_status"),
        CheckConstraint("mode IN ('llm','heuristic','provider','mixed')", name="ck_agent_runs_mode"),
        CheckConstraint("token_usage >= 0 AND duration_ms >= 0", name="ck_agent_runs_usage"),
        CheckConstraint("run_generation >= 0", name="ck_agent_runs_run_generation"),
        Index("ix_agent_runs_case_generation", "case_id", "run_generation"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(40), ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    agent: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(15), default="running")
    mode: Mapped[str] = mapped_column(String(15), default="heuristic")
    input_summary: Mapped[str] = mapped_column(Text, default="")
    output_summary: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_usage: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # ---- fencing 元数据：本次运行所属 generation / attempt 与阶段 ----
    run_generation: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(40), default="")
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    reused_from_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str] = mapped_column(String(60), default="")
    error_kind: Mapped[str] = mapped_column(String(30), default="")

    case: Mapped[Case] = relationship(back_populates="agent_runs")


class CaseRunAttempt(Base):
    """一次被 lease 认领的执行尝试；用于恢复、排障与并发 fencing 观测。"""
    __tablename__ = "case_run_attempts"
    __table_args__ = (
        UniqueConstraint("case_id", "attempt", name="uq_case_run_attempts_case_attempt"),
        CheckConstraint("attempt >= 0", name="ck_case_run_attempts_attempt"),
        CheckConstraint("run_generation >= 0", name="ck_case_run_attempts_run_generation"),
        CheckConstraint("status IN ('running','done','failed','interrupted')", name="ck_case_run_attempts_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(String(40), ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    run_generation: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str] = mapped_column(String(60), default="")
    lease_owner: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[str] = mapped_column(String(15), default="running", index=True)
    resume_from_stage: Mapped[str] = mapped_column(String(40), default="")
    last_stage: Mapped[str] = mapped_column(String(40), default="")
    failure_stage: Mapped[str] = mapped_column(String(40), default="")
    failure_kind: Mapped[str] = mapped_column(String(30), default="")
    failure_code: Mapped[str] = mapped_column(String(60), default="")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CrossExamination(Base):
    __tablename__ = "cross_examinations"
    __table_args__ = (
        UniqueConstraint("id", "case_id", name="uq_cross_examinations_id_case"),
        ForeignKeyConstraint(["target_argument_id", "case_id"], ["arguments.id", "arguments.case_id"], name="fk_cross_exam_target_argument_case", ondelete="CASCADE"),
        CheckConstraint("target_side IN ('prosecution','defense','both')", name="ck_cross_exam_target_side"),
        CheckConstraint("severity IN ('low','medium','high')", name="ck_cross_exam_severity"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(40), ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    target_side: Mapped[str] = mapped_column(String(15))
    target_argument_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    issue_type: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10), default="medium")
    description: Mapped[str] = mapped_column(Text)
    related_evidence_ids: Mapped[list] = mapped_column(JSONList, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    case: Mapped[Case] = relationship(back_populates="cross_examinations", foreign_keys=[case_id])


class UserQuestion(Base):
    __tablename__ = "user_questions"
    __table_args__ = (
        UniqueConstraint("id", "case_id", name="uq_user_questions_id_case"),
        CheckConstraint("target IN ('prosecution','defense','judge','evidence','source')", name="ck_user_questions_target"),
        CheckConstraint("challenge_type IN ('fact','logic','evidence','definition','scope','source','other')", name="ck_user_questions_challenge_type"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(40), ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str | None] = mapped_column(String(40), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    target: Mapped[str] = mapped_column(String(30))
    target_ref_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    challenge_type: Mapped[str] = mapped_column(String(30), default="other")
    response: Mapped[str] = mapped_column(Text, default="")
    related_evidence_ids: Mapped[list] = mapped_column(JSONList, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    case: Mapped[Case] = relationship(back_populates="user_questions")


class Verdict(Base):
    __tablename__ = "verdicts"
    __table_args__ = (
        UniqueConstraint("case_id", name="uq_verdicts_case_id"),
        UniqueConstraint("id", "case_id", name="uq_verdicts_id_case"),
        CheckConstraint("conclusion_stance IN ('prosecution','defense','conditional','insufficient')", name="ck_verdicts_stance"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_verdicts_confidence"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(40), ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    conclusion: Mapped[str] = mapped_column(Text)
    conclusion_stance: Mapped[str] = mapped_column(String(20), default="conditional")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    prosecution_summary: Mapped[str] = mapped_column(Text, default="")
    defense_summary: Mapped[str] = mapped_column(Text, default="")
    shared_facts: Mapped[list] = mapped_column(JSONList, default=list)
    core_disputes: Mapped[list] = mapped_column(JSONList, default=list)
    strongest_evidence_ids: Mapped[list] = mapped_column(JSONList, default=list)
    strongest_counter_evidence_ids: Mapped[list] = mapped_column(JSONList, default=list)
    evidence_gaps: Mapped[list] = mapped_column(JSONList, default=list)
    definition_conflicts: Mapped[list] = mapped_column(JSONList, default=list)
    unknowns: Mapped[list] = mapped_column(JSONList, default=list)
    verdict_changers: Mapped[list] = mapped_column(JSONList, default=list)
    next_questions: Mapped[list] = mapped_column(JSONList, default=list)
    cross_exam_summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    case: Mapped[Case] = relationship(back_populates="verdict")


class ApiUsage(Base):
    __tablename__ = "api_usage"
    __table_args__ = (
        UniqueConstraint("provider", "usage_date", name="uq_api_usage_provider_date"),
        CheckConstraint("calls >= 0 AND cache_hits >= 0 AND cache_misses >= 0 AND failures >= 0", name="ck_api_usage_counts"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(30), index=True)
    endpoint: Mapped[str] = mapped_column(String(120), default="")
    usage_date: Mapped[str] = mapped_column(String(10), index=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    cache_hits: Mapped[int] = mapped_column(Integer, default=0)
    cache_misses: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)


class ClaimEvidence(Base):
    __tablename__ = "claim_evidence"
    __table_args__ = (
        ForeignKeyConstraint(["claim_id", "case_id"], ["claims.id", "claims.case_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["evidence_id", "case_id"], ["evidence.id", "evidence.case_id"], ondelete="CASCADE"),
    )
    case_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    claim_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(40), primary_key=True)


class ArgumentEvidence(Base):
    __tablename__ = "argument_evidence"
    __table_args__ = (
        ForeignKeyConstraint(["argument_id", "case_id"], ["arguments.id", "arguments.case_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["evidence_id", "case_id"], ["evidence.id", "evidence.case_id"], ondelete="CASCADE"),
    )
    case_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    argument_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(40), primary_key=True)


class ArgumentClaim(Base):
    __tablename__ = "argument_claim"
    __table_args__ = (
        ForeignKeyConstraint(["argument_id", "case_id"], ["arguments.id", "arguments.case_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["claim_id", "case_id"], ["claims.id", "claims.case_id"], ondelete="CASCADE"),
    )
    case_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    argument_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    claim_id: Mapped[str] = mapped_column(String(40), primary_key=True)


class VerdictStrongestEvidence(Base):
    __tablename__ = "verdict_strongest_evidence"
    __table_args__ = (
        ForeignKeyConstraint(["verdict_id", "case_id"], ["verdicts.id", "verdicts.case_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["evidence_id", "case_id"], ["evidence.id", "evidence.case_id"], ondelete="CASCADE"),
    )
    case_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    verdict_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(40), primary_key=True)


class VerdictCounterEvidence(Base):
    __tablename__ = "verdict_counter_evidence"
    __table_args__ = (
        ForeignKeyConstraint(["verdict_id", "case_id"], ["verdicts.id", "verdicts.case_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["evidence_id", "case_id"], ["evidence.id", "evidence.case_id"], ondelete="CASCADE"),
    )
    case_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    verdict_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(40), primary_key=True)


class CrossExamEvidence(Base):
    __tablename__ = "cross_exam_evidence"
    __table_args__ = (
        ForeignKeyConstraint(["cross_exam_id", "case_id"], ["cross_examinations.id", "cross_examinations.case_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["evidence_id", "case_id"], ["evidence.id", "evidence.case_id"], ondelete="CASCADE"),
    )
    case_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    cross_exam_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(40), primary_key=True)


class UserQuestionEvidence(Base):
    __tablename__ = "user_question_evidence"
    __table_args__ = (
        ForeignKeyConstraint(["user_question_id", "case_id"], ["user_questions.id", "user_questions.case_id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["evidence_id", "case_id"], ["evidence.id", "evidence.case_id"], ondelete="CASCADE"),
    )
    case_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_question_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(40), primary_key=True)


_LINK_SPECS = (
    (Claim, "evidence_ids", ClaimEvidence, "claim_id", Evidence),
    (Argument, "evidence_ids", ArgumentEvidence, "argument_id", Evidence),
    (Argument, "claim_ids", ArgumentClaim, "argument_id", Claim),
    (Verdict, "strongest_evidence_ids", VerdictStrongestEvidence, "verdict_id", Evidence),
    (Verdict, "strongest_counter_evidence_ids", VerdictCounterEvidence, "verdict_id", Evidence),
    (CrossExamination, "related_evidence_ids", CrossExamEvidence, "cross_exam_id", Evidence),
    (UserQuestion, "related_evidence_ids", UserQuestionEvidence, "user_question_id", Evidence),
)


def _clean_reference_ids(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return list(dict.fromkeys(item for item in value if isinstance(item, str) and item))


def _normalize_verdict_references(session: Session, verdict: Verdict, pending: dict) -> None:
    """在写 JSON 与规范关系表前强制执行判决证据语义不变式。"""
    support = _clean_reference_ids(verdict.strongest_evidence_ids)
    counter = _clean_reference_ids(verdict.strongest_counter_evidence_ids)
    if verdict.conclusion_stance in ("insufficient", "conditional"):
        verdict.strongest_evidence_ids = []
        verdict.strongest_counter_evidence_ids = []
        return

    expected = {
        "prosecution": ("pro", "con"),
        "defense": ("con", "pro"),
    }.get(verdict.conclusion_stance)
    if expected is None:
        return
    overlap = sorted(set(support) & set(counter))
    if overlap:
        raise ValueError(f"Verdict strongest evidence sets overlap: {overlap}")
    if not support or not counter:
        raise ValueError("Decisive verdict requires both strongest support and counter evidence")

    def validate(ids: list[str], stance: str, attr: str) -> None:
        invalid: list[str] = []
        for evidence_id in ids:
            item = pending.get((Evidence, evidence_id)) or session.get(Evidence, evidence_id)
            if item is None or item.case_id != verdict.case_id or item.stance != stance:
                invalid.append(evidence_id)
                continue
            source = pending.get((Source, item.source_id)) or session.get(Source, item.source_id)
            if source is None or source.case_id != verdict.case_id or source.is_demo:
                invalid.append(evidence_id)
        if invalid:
            raise ValueError(
                f"Verdict.{attr} violates stance/non-Mock/case invariant: {invalid}"
            )

    validate(support, expected[0], "strongest_evidence_ids")
    validate(counter, expected[1], "strongest_counter_evidence_ids")
    verdict.strongest_evidence_ids = support
    verdict.strongest_counter_evidence_ids = counter


@event.listens_for(Session, "before_flush")
def _stage_compatibility_links(session: Session, _flush_context, _instances) -> None:
    """以兼容 JSON 为写入入口，在同一事务内同步规范关系表。"""
    pending = {(type(obj), getattr(obj, "id", None)): obj for obj in session.new}
    for obj in tuple(session.new) + tuple(session.dirty):
        if isinstance(obj, Verdict) and obj not in session.deleted:
            _normalize_verdict_references(session, obj, pending)
    staged: list[tuple[type[Base], str, str, str, list[str]]] = []
    for owner_type, attr, link_type, owner_col, target_type in _LINK_SPECS:
        for owner in tuple(session.new) + tuple(session.dirty):
            if not isinstance(owner, owner_type) or owner in session.deleted:
                continue
            if owner in session.dirty and not inspect(owner).attrs[attr].history.has_changes():
                continue
            ids = _clean_reference_ids(getattr(owner, attr, []))
            setattr(owner, attr, ids)
            if not getattr(owner, "id", None) or not getattr(owner, "case_id", None):
                continue
            found: dict[str, str] = {}
            for target_id in ids:
                target = pending.get((target_type, target_id)) or session.get(target_type, target_id)
                if target is not None:
                    found[target_id] = target.case_id
            missing = [target_id for target_id in ids if target_id not in found]
            cross_case = [target_id for target_id, case_id in found.items() if case_id != owner.case_id]
            if missing or cross_case:
                raise ValueError(
                    f"{owner_type.__name__}.{attr} contains invalid references "
                    f"(missing={missing}, cross_case={cross_case})"
                )
            staged.append((link_type, owner_col, owner.id, owner.case_id, ids))
    if staged:
        session.info.setdefault("compatibility_links", []).extend(staged)


@event.listens_for(Session, "after_soft_rollback")
def _discard_rolled_back_links(session: Session, _previous_transaction) -> None:
    session.info.pop("compatibility_links", None)


@event.listens_for(Session, "after_flush_postexec")
def _write_compatibility_links(session: Session, _flush_context) -> None:
    staged = session.info.pop("compatibility_links", [])
    if not staged:
        return
    connection = session.connection()
    for link_type, owner_col, owner_id, case_id, target_ids in staged:
        table = link_type.__table__
        connection.execute(table.delete().where(table.c[owner_col] == owner_id))
        target_col = next(name for name in ("evidence_id", "claim_id") if name in table.c and name != owner_col)
        if target_ids:
            connection.execute(
                table.insert(),
                [{"case_id": case_id, owner_col: owner_id, target_col: target_id} for target_id in target_ids],
            )
