"""allow reused agent-run records in the database status constraint

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-13

The ORM and workflow already use ``reused`` when a completed stage is reused
after a retry, while the 0004 database constraint only allowed the original
four states. Replace that constraint without changing any data. Inspector
results differ between SQLite, MariaDB and MySQL versions, so the migration is
careful to be idempotent when the existing CHECK is visible and simply creates
the named constraint when it is not reflected.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_CONSTRAINT_NAME = "ck_agent_runs_status"
_STATUS_CHECK = "status IN ('running','done','failed','skipped','reused')"
_NAMING = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}


def _status_check(bind) -> dict[str, object] | None:
    if "agent_runs" not in sa.inspect(bind).get_table_names():
        return None
    for constraint in sa.inspect(bind).get_check_constraints("agent_runs"):
        if constraint.get("name") == _CONSTRAINT_NAME:
            return constraint
    return None


def upgrade() -> None:
    bind = op.get_bind()
    if "agent_runs" not in sa.inspect(bind).get_table_names():
        return

    existing = _status_check(bind)
    expression = str((existing or {}).get("sqltext") or "").lower()
    if "reused" in expression:
        return

    with op.batch_alter_table("agent_runs", naming_convention=_NAMING) as batch:
        if existing is not None:
            batch.drop_constraint(_CONSTRAINT_NAME, type_="check")
        batch.create_check_constraint(_CONSTRAINT_NAME, _STATUS_CHECK)


def downgrade() -> None:
    raise RuntimeError(
        "0009 permits persisted reused runs; restore a verified pre-upgrade backup instead"
    )
