"""case execution fencing (chained after 0006 oauth state hardening): run generation, lease, attempts, failure taxonomy

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-13

- cases 新增 run_generation / lease_* / run_started_at / resume_from_stage /
  last_completed_stage / failure_* / retry_count 列与约束索引。
- 新增 case_run_attempts 表（每次 lease 认领一条）。
- 回填：存量案件 run_generation = 0（server_default 覆盖）。
- 安全策略：升级时遗留 running（服务中断）与 queued（租约从未建立）一律转
  failed，failure_kind='interrupted'，交由用户通过重新审理发起新 generation；
  同时清理 lease 字段。
- 幂等：所有列/表/索引/约束均先检查再创建，可重复执行。
- SQLite / MariaDB 兼容：列新增走 ADD COLUMN；约束重建走 batch_alter_table。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_NAMING = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}

_NEW_CASE_COLUMNS: list[tuple[str, sa.types.TypeEngine, bool, object]] = [
    ("run_generation", sa.Integer(), False, 0),
    ("lease_token", sa.String(60), False, ""),
    ("lease_owner", sa.String(80), False, ""),
    ("lease_expires_at", sa.DateTime(), True, None),
    ("lease_heartbeat_at", sa.DateTime(), True, None),
    ("run_started_at", sa.DateTime(), True, None),
    ("resume_from_stage", sa.String(40), False, ""),
    ("last_completed_stage", sa.String(40), False, ""),
    ("failure_stage", sa.String(40), False, ""),
    ("failure_kind", sa.String(30), False, ""),
    ("failure_code", sa.String(60), False, ""),
    ("retry_count", sa.Integer(), False, 0),
]


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def _columns(bind, table: str) -> set[str]:
    return {col["name"] for col in sa.inspect(bind).get_columns(table)}


def _indexes(bind, table: str) -> set[str]:
    return {ix["name"] for ix in sa.inspect(bind).get_indexes(table)}


def _check_constraints(bind, table: str) -> set[str]:
    return {ck["name"] for ck in sa.inspect(bind).get_check_constraints(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(bind, "cases"):
        return  # 空库会由 Base.metadata 建表路径覆盖；防止误升级非本案 schema

    # 1) cases 新列（幂等）
    existing_columns = _columns(bind, "cases")
    for name, col_type, nullable, default in _NEW_CASE_COLUMNS:
        if name in existing_columns:
            continue
        if isinstance(default, int):
            server_default = sa.text(str(default))
        elif default == "":
            server_default = ""
        else:
            server_default = None
        op.add_column("cases", sa.Column(name, col_type, nullable=nullable, server_default=server_default))

    # 2) 约束与索引（幂等；batch 兼容 SQLite 重建）
    existing_checks = _check_constraints(bind, "cases")
    with op.batch_alter_table("cases", naming_convention=_NAMING) as batch:
        if "ck_cases_run_generation" not in existing_checks:
            batch.create_check_constraint("ck_cases_run_generation", "run_generation >= 0")
        if "ck_cases_retry_count" not in existing_checks:
            batch.create_check_constraint("ck_cases_retry_count", "retry_count >= 0")
    if "ix_cases_status_generation" not in _indexes(bind, "cases"):
        op.create_index("ix_cases_status_generation", "cases", ["status", "run_generation"])

    # 3) case_run_attempts 表（幂等）
    if not _has_table(bind, "case_run_attempts"):
        op.create_table(
            "case_run_attempts",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("case_id", sa.String(40), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("attempt", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("run_generation", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("lease_token", sa.String(60), nullable=False, server_default=""),
            sa.Column("lease_owner", sa.String(80), nullable=False, server_default=""),
            sa.Column("status", sa.String(15), nullable=False, server_default="running", index=True),
            sa.Column("resume_from_stage", sa.String(40), nullable=False, server_default=""),
            sa.Column("last_stage", sa.String(40), nullable=False, server_default=""),
            sa.Column("failure_stage", sa.String(40), nullable=False, server_default=""),
            sa.Column("failure_kind", sa.String(30), nullable=False, server_default=""),
            sa.Column("failure_code", sa.String(60), nullable=False, server_default=""),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("case_id", "attempt", name="uq_case_run_attempts_case_attempt"),
            sa.CheckConstraint("attempt >= 0", name="ck_case_run_attempts_attempt"),
            sa.CheckConstraint("run_generation >= 0", name="ck_case_run_attempts_run_generation"),
            sa.CheckConstraint("status IN ('running','done','failed','interrupted')", name="ck_case_run_attempts_status"),
        )

    # 4) 回填与安全策略（幂等）：generation 0 由 server_default 覆盖；
    #    遗留 running / queued 转 interrupted-failed 并清理 lease。
    bind.execute(
        sa.text(
            "UPDATE cases SET status = 'failed', current_stage = 'failed', "
            "failure_kind = 'interrupted', failure_stage = CASE WHEN failure_stage = '' OR failure_stage IS NULL THEN COALESCE(current_stage, '') ELSE failure_stage END, "
            "error_message = COALESCE(NULLIF(error_message, ''), '服务升级中断了本次审理，请重新审理。'), "
            "lease_token = '', lease_owner = '', lease_expires_at = NULL, lease_heartbeat_at = NULL "
            "WHERE status IN ('running', 'queued')"
        )
    )


def downgrade() -> None:
    raise RuntimeError("0006 introduces execution fencing state; restore a verified pre-upgrade backup instead")
