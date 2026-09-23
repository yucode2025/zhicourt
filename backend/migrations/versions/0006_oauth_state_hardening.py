"""OAuth state context fields and legacy token cleanup.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-13
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_NAMING = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}


def _columns(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    existing = _columns(bind, "zhihu_oauth_states")
    if "purpose" not in existing:
        op.add_column(
            "zhihu_oauth_states",
            sa.Column("purpose", sa.String(10), nullable=False, server_default="login"),
        )
    if "initiating_user_id" not in existing:
        # SQLite 不支持 ALTER ADD 约束；带 FK 的列必须走 batch（表重建），MariaDB 兼容。
        with op.batch_alter_table("zhihu_oauth_states", naming_convention=_NAMING) as batch:
            batch.add_column(
                sa.Column(
                    "initiating_user_id",
                    sa.String(40),
                    sa.ForeignKey(
                        "users.id", ondelete="SET NULL",
                        name="fk_zhihu_oauth_states_initiating_user_id_users",
                    ),
                    nullable=True,
                )
            )
    if "initiating_session_hash" not in existing:
        op.add_column(
            "zhihu_oauth_states",
            sa.Column("initiating_session_hash", sa.String(64), nullable=True),
        )
    if "anonymous_session_hash" not in existing:
        op.add_column(
            "zhihu_oauth_states",
            sa.Column("anonymous_session_hash", sa.String(64), nullable=True),
        )

    columns = _columns(bind, "zhihu_oauth_states")
    if "initiating_user_id" in columns:
        index_names = {index["name"] for index in sa.inspect(bind).get_indexes("zhihu_oauth_states")}
        if "ix_zhihu_oauth_states_initiating_user_id" not in index_names:
            op.create_index(
                "ix_zhihu_oauth_states_initiating_user_id",
                "zhihu_oauth_states",
                ["initiating_user_id"],
            )

    if "zhihu_oauth_accounts" in sa.inspect(bind).get_table_names():
        bind.execute(
            sa.text(
                "UPDATE zhihu_oauth_accounts "
                "SET access_token = :empty, token_expires_at = NULL"
            ),
            {"empty": ""},
        )


def downgrade() -> None:
    raise RuntimeError("0006 removes OAuth state context and cannot be downgraded safely")
