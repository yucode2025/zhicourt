"""productization: users 扩展 / sessions / favorites / audit / settings / case 公开分享

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-06
"""
from __future__ import annotations

import uuid

from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    return column in [c["name"] for c in insp.get_columns(table)]


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()

    # ---- users 扩展（增量，不动已有数据）----
    if not _has_column(bind, "users", "username"):
        op.add_column("users", sa.Column("username", sa.String(50), nullable=True))
        op.create_index("ix_users_username", "users", ["username"], unique=True)
    if not _has_column(bind, "users", "password_hash"):
        op.add_column("users", sa.Column("password_hash", sa.String(200), nullable=True))
    if not _has_column(bind, "users", "nickname"):
        op.add_column("users", sa.Column("nickname", sa.String(50), nullable=True))
    if not _has_column(bind, "users", "avatar"):
        op.add_column("users", sa.Column("avatar", sa.String(20), nullable=True))
    if not _has_column(bind, "users", "role"):
        op.add_column("users", sa.Column("role", sa.String(15), nullable=False, server_default="user"))
        op.create_index("ix_users_role", "users", ["role"])
    if not _has_column(bind, "users", "status"):
        op.add_column("users", sa.Column("status", sa.String(15), nullable=False, server_default="active"))
    if not _has_column(bind, "users", "last_login_at"):
        op.add_column("users", sa.Column("last_login_at", sa.DateTime(), nullable=True))

    # ---- cases 公开分享字段 ----
    if not _has_column(bind, "cases", "is_public"):
        op.add_column("cases", sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.text("1")))
        op.create_index("ix_cases_is_public", "cases", ["is_public"])
    if not _has_column(bind, "cases", "public_id"):
        op.add_column("cases", sa.Column("public_id", sa.String(30), nullable=True))
        op.create_index("ix_cases_public_id", "cases", ["public_id"], unique=True)
        # 为存量案件回填 public_id
        cases = bind.execute(sa.text("SELECT id FROM cases WHERE public_id IS NULL")).fetchall()
        for (cid,) in cases:
            bind.execute(
                sa.text("UPDATE cases SET public_id = :pid WHERE id = :cid"),
                {"pid": uuid.uuid4().hex[:20], "cid": cid},
            )
    if not _has_column(bind, "cases", "finished_at"):
        op.add_column("cases", sa.Column("finished_at", sa.DateTime(), nullable=True))

    # ---- 新表 ----
    if not _has_table(bind, "user_sessions"):
        op.create_table(
            "user_sessions",
            sa.Column("id", sa.String(60), primary_key=True),
            sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id"), index=True),
            sa.Column("created_at", sa.DateTime()),
            sa.Column("expires_at", sa.DateTime(), index=True),
            sa.Column("ip", sa.String(45)),
        )
    if not _has_table(bind, "favorites"):
        op.create_table(
            "favorites",
            sa.Column("id", sa.String(40), primary_key=True),
            sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id"), index=True),
            sa.Column("case_id", sa.String(40), sa.ForeignKey("cases.id"), index=True),
            sa.Column("created_at", sa.DateTime()),
        )
    if not _has_table(bind, "admin_audit_logs"):
        op.create_table(
            "admin_audit_logs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("admin_user_id", sa.String(40), index=True),
            sa.Column("action", sa.String(50)),
            sa.Column("target", sa.String(120)),
            sa.Column("detail", sa.String(500)),
            sa.Column("ip", sa.String(45)),
            sa.Column("result", sa.String(10)),
            sa.Column("created_at", sa.DateTime(), index=True),
        )
    if not _has_table(bind, "system_settings"):
        op.create_table(
            "system_settings",
            sa.Column("key", sa.String(60), primary_key=True),
            sa.Column("value", sa.JSON()),
            sa.Column("updated_at", sa.DateTime()),
            sa.Column("updated_by", sa.String(40)),
        )


def downgrade() -> None:
    op.drop_table("system_settings")
    op.drop_table("admin_audit_logs")
    op.drop_table("favorites")
    op.drop_table("user_sessions")
    op.drop_index("ix_cases_public_id", table_name="cases")
    op.drop_column("cases", "public_id")
    op.drop_index("ix_cases_is_public", table_name="cases")
    op.drop_column("cases", "is_public")
    op.drop_column("cases", "finished_at")
    op.drop_index("ix_users_role", table_name="users")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "status")
    op.drop_column("users", "role")
    op.drop_column("users", "avatar")
    op.drop_column("users", "nickname")
    op.drop_column("users", "password_hash")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_column("users", "username")
