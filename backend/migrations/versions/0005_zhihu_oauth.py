"""zhihu oauth login: state table, account binding, account_kind 'zhihu'

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-12
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "zhihu_oauth_states"):
        op.create_table(
            "zhihu_oauth_states",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("expires_at", sa.DateTime(), nullable=False, index=True),
            sa.Column("consumed", sa.Boolean(), nullable=False, server_default=sa.text("0"), index=True),
            sa.Column("ip", sa.String(45), nullable=False, server_default=""),
        )

    if not _has_table(bind, "zhihu_oauth_accounts"):
        op.create_table(
            "zhihu_oauth_accounts",
            sa.Column("id", sa.String(40), primary_key=True),
            sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True),
            sa.Column("zhihu_uid", sa.String(40), nullable=False, unique=True, index=True),
            sa.Column("zhihu_hash_id", sa.String(80), nullable=False, server_default=""),
            sa.Column("zhihu_nickname", sa.String(120), nullable=True),
            sa.Column("zhihu_avatar_url", sa.String(500), nullable=True),
            sa.Column("access_token", sa.String(2048), nullable=False, server_default=""),
            sa.Column("token_expires_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )

    # account_kind 扩展 'zhihu'：仅当旧约束仍为两值枚举时重建
    existing = {item.get("name"): item for item in sa.inspect(bind).get_check_constraints("users")}
    old = existing.get("ck_users_account_kind")
    if old is not None and "zhihu" not in (old.get("sqltext") or old.get("expression") or ""):
        # The reflected constraint is already named. A naming convention here
        # prefixes it a second time on SQLite, so use its reflected name.
        with op.batch_alter_table("users") as batch:
            batch.drop_constraint(old["name"], type_="check")
            batch.create_check_constraint("ck_users_account_kind", "account_kind IN ('member','anonymous','zhihu')")


def downgrade() -> None:
    raise RuntimeError("0005 introduces zhihu oauth tables; restore a verified pre-upgrade backup instead")
