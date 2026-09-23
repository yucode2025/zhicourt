"""repair agent run execution metadata omitted by 0007

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-13

0007 introduced execution fencing but did not add the corresponding metadata
columns to the existing ``agent_runs`` table.  Fresh ORM queries therefore
failed on upgraded databases.  This repair is intentionally idempotent so it
also handles databases where some columns were added manually.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_NAMING = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}

_NEW_COLUMNS: list[tuple[str, sa.types.TypeEngine, bool, object]] = [
    ("run_generation", sa.Integer(), False, 0),
    ("stage", sa.String(40), False, ""),
    ("attempt", sa.Integer(), False, 0),
    ("reused_from_generation", sa.Integer(), True, None),
    ("error_code", sa.String(60), False, ""),
    ("error_kind", sa.String(30), False, ""),
]


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def _columns(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def _indexes(bind, table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(bind).get_indexes(table)}


def _checks(bind, table: str) -> set[str]:
    return {check["name"] for check in sa.inspect(bind).get_check_constraints(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "agent_runs"):
        return

    existing_columns = _columns(bind, "agent_runs")
    for name, column_type, nullable, default in _NEW_COLUMNS:
        if name in existing_columns:
            continue
        if isinstance(default, int):
            server_default = sa.text(str(default))
        elif default == "":
            server_default = ""
        else:
            server_default = None
        op.add_column(
            "agent_runs",
            sa.Column(name, column_type, nullable=nullable, server_default=server_default),
        )

    existing_checks = _checks(bind, "agent_runs")
    if "ck_agent_runs_run_generation" not in existing_checks:
        with op.batch_alter_table("agent_runs", naming_convention=_NAMING) as batch:
            batch.create_check_constraint(
                "ck_agent_runs_run_generation", "run_generation >= 0"
            )

    if "ix_agent_runs_case_generation" not in _indexes(bind, "agent_runs"):
        op.create_index(
            "ix_agent_runs_case_generation",
            "agent_runs",
            ["case_id", "run_generation"],
        )


def downgrade() -> None:
    raise RuntimeError(
        "0008 repairs required execution metadata; restore a verified pre-upgrade backup instead"
    )
