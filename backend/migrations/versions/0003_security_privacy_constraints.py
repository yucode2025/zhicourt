"""security, privacy and concurrency constraints

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-12
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _has_unique(bind, table: str, name: str) -> bool:
    insp = sa.inspect(bind)
    names = {c.get("name") for c in insp.get_unique_constraints(table)}
    names.update(i.get("name") for i in insp.get_indexes(table) if i.get("unique"))
    return name in names


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_column(bind, "users", "account_kind"):
        op.add_column("users", sa.Column("account_kind", sa.String(15), nullable=True))
        bind.execute(sa.text(
            "UPDATE users SET account_kind = CASE WHEN username IS NULL AND password_hash IS NULL "
            "THEN 'anonymous' ELSE 'member' END WHERE account_kind IS NULL"
        ))
        with op.batch_alter_table("users") as batch:
            batch.alter_column("account_kind", existing_type=sa.String(15), nullable=False, server_default="member")
            batch.create_index("ix_users_account_kind", ["account_kind"])

    if not _has_table(bind, "anonymous_sessions"):
        op.create_table(
            "anonymous_sessions",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("status", sa.String(15), nullable=False, server_default="active"),
            sa.Column("migrated_to_user_id", sa.String(40), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("ip", sa.String(45), nullable=False, server_default=""),
            sa.UniqueConstraint("user_id", name="uq_anonymous_sessions_user_id"),
        )
        op.create_index("ix_anonymous_sessions_user_id", "anonymous_sessions", ["user_id"])
        op.create_index("ix_anonymous_sessions_expires_at", "anonymous_sessions", ["expires_at"])
        op.create_index("ix_anonymous_sessions_status", "anonymous_sessions", ["status"])

    if not _has_column(bind, "user_questions", "user_id"):
        op.add_column("user_questions", sa.Column("user_id", sa.String(40), nullable=True))
        op.create_index("ix_user_questions_user_id", "user_questions", ["user_id"])
        with op.batch_alter_table("user_questions") as batch:
            batch.create_foreign_key("fk_user_questions_user_id", "users", ["user_id"], ["id"])

    # 只改变未来记录的数据库默认值，现有公开状态和分享链接保持不变。
    with op.batch_alter_table("cases") as batch:
        batch.alter_column("is_public", existing_type=sa.Boolean(), nullable=False, server_default=sa.text("0"))

    if not _has_unique(bind, "favorites", "uq_favorites_user_case"):
        duplicate_favorites = bind.execute(sa.text(
            "SELECT user_id, case_id, MIN(id) keep_id FROM favorites "
            "GROUP BY user_id, case_id HAVING COUNT(*) > 1"
        )).fetchall()
        for user_id, case_id, keep_id in duplicate_favorites:
            bind.execute(sa.text(
                "DELETE FROM favorites WHERE user_id=:uid AND case_id=:cid AND id<>:keep"
            ), {"uid": user_id, "cid": case_id, "keep": keep_id})
        with op.batch_alter_table("favorites") as batch:
            batch.create_unique_constraint("uq_favorites_user_case", ["user_id", "case_id"])

    if not _has_unique(bind, "api_usage", "uq_api_usage_provider_date"):
        groups = bind.execute(sa.text(
            "SELECT provider, usage_date, MIN(id) keep_id, "
            "SUM(COALESCE(calls,0)) calls, SUM(COALESCE(cache_hits,0)) hits, "
            "SUM(COALESCE(cache_misses,0)) misses, SUM(COALESCE(failures,0)) failures "
            "FROM api_usage GROUP BY provider, usage_date HAVING COUNT(*) > 1"
        )).fetchall()
        for provider, day, keep_id, calls, hits, misses, failures in groups:
            bind.execute(sa.text(
                "UPDATE api_usage SET calls=:calls, cache_hits=:hits, cache_misses=:misses, failures=:failures "
                "WHERE id=:keep"
            ), {"calls": calls, "hits": hits, "misses": misses, "failures": failures, "keep": keep_id})
            bind.execute(sa.text(
                "DELETE FROM api_usage WHERE provider=:provider AND usage_date=:day AND id<>:keep"
            ), {"provider": provider, "day": day, "keep": keep_id})
        bind.execute(sa.text(
            "UPDATE api_usage SET calls=COALESCE(calls,0), cache_hits=COALESCE(cache_hits,0), "
            "cache_misses=COALESCE(cache_misses,0), failures=COALESCE(failures,0)"
        ))
        with op.batch_alter_table("api_usage") as batch:
            batch.create_unique_constraint("uq_api_usage_provider_date", ["provider", "usage_date"])


def downgrade() -> None:
    raise RuntimeError("0003 contains privacy and deduplication changes; restore a verified pre-upgrade backup instead")
