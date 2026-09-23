"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-06
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "cases",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("user_id", sa.String(length=40), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("original_question", sa.Text(), nullable=False),
        sa.Column("proposition", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("current_stage", sa.String(length=40), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=True),
        sa.Column("progress_events", sa.JSON(), nullable=False),
        sa.Column("progress_index", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("engine_mode", sa.String(length=20), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_cases_created_at", "cases", ["created_at"], unique=False)
    op.create_index("ix_cases_status", "cases", ["status"], unique=False)
    op.create_index("ix_cases_user_id", "cases", ["user_id"], unique=False)

    op.create_table(
        "sources",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("case_id", sa.String(length=40), nullable=False),
        sa.Column("origin", sa.String(length=10), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("author", sa.String(length=120), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("published_at", sa.String(length=30), nullable=False),
        sa.Column("vote_count", sa.Integer(), nullable=False),
        sa.Column("comment_count", sa.Integer(), nullable=False),
        sa.Column("relevance_score", sa.Float(), nullable=False),
        sa.Column("authority_score", sa.Float(), nullable=False),
        sa.Column("community_score", sa.Float(), nullable=False),
        sa.Column("recency_score", sa.Float(), nullable=False),
        sa.Column("independence_score", sa.Float(), nullable=False),
        sa.Column("rank_score", sa.Float(), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sources_case_id", "sources", ["case_id"], unique=False)

    op.create_table(
        "evidence",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("case_id", sa.String(length=40), nullable=False),
        sa.Column("source_id", sa.String(length=40), nullable=False),
        sa.Column("claim", sa.Text(), nullable=False),
        sa.Column("stance", sa.String(length=10), nullable=False),
        sa.Column("evidence_type", sa.String(length=15), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("quoted_fragment", sa.Text(), nullable=True),
        sa.Column("relevance_score", sa.Float(), nullable=False),
        sa.Column("authority_score", sa.Float(), nullable=False),
        sa.Column("strength", sa.Float(), nullable=False),
        sa.Column("limitations", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evidence_case_id", "evidence", ["case_id"], unique=False)
    op.create_index("ix_evidence_source_id", "evidence", ["source_id"], unique=False)

    op.create_table(
        "claims",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("case_id", sa.String(length=40), nullable=False),
        sa.Column("side", sa.String(length=10), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_claims_case_id", "claims", ["case_id"], unique=False)

    op.create_table(
        "arguments",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("case_id", sa.String(length=40), nullable=False),
        sa.Column("side", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("strength", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_arguments_case_id", "arguments", ["case_id"], unique=False)

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("case_id", sa.String(length=40), nullable=False),
        sa.Column("agent", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=15), nullable=False),
        sa.Column("mode", sa.String(length=15), nullable=False),
        sa.Column("input_summary", sa.Text(), nullable=False),
        sa.Column("output_summary", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("token_usage", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_runs_case_id", "agent_runs", ["case_id"], unique=False)

    op.create_table(
        "cross_examinations",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("case_id", sa.String(length=40), nullable=False),
        sa.Column("target_side", sa.String(length=15), nullable=False),
        sa.Column("target_argument_id", sa.String(length=40), nullable=True),
        sa.Column("issue_type", sa.String(length=40), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("related_evidence_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_cross_examinations_case_id",
        "cross_examinations",
        ["case_id"],
        unique=False,
    )

    op.create_table(
        "user_questions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("case_id", sa.String(length=40), nullable=False),
        sa.Column("target", sa.String(length=30), nullable=False),
        sa.Column("target_ref_id", sa.String(length=40), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("challenge_type", sa.String(length=30), nullable=False),
        sa.Column("response", sa.Text(), nullable=False),
        sa.Column("related_evidence_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_questions_case_id", "user_questions", ["case_id"], unique=False)

    op.create_table(
        "verdicts",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("case_id", sa.String(length=40), nullable=False),
        sa.Column("conclusion", sa.Text(), nullable=False),
        sa.Column("conclusion_stance", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("prosecution_summary", sa.Text(), nullable=False),
        sa.Column("defense_summary", sa.Text(), nullable=False),
        sa.Column("shared_facts", sa.JSON(), nullable=False),
        sa.Column("core_disputes", sa.JSON(), nullable=False),
        sa.Column("strongest_evidence_ids", sa.JSON(), nullable=False),
        sa.Column("strongest_counter_evidence_ids", sa.JSON(), nullable=False),
        sa.Column("evidence_gaps", sa.JSON(), nullable=False),
        sa.Column("definition_conflicts", sa.JSON(), nullable=False),
        sa.Column("unknowns", sa.JSON(), nullable=False),
        sa.Column("verdict_changers", sa.JSON(), nullable=False),
        sa.Column("next_questions", sa.JSON(), nullable=False),
        sa.Column("cross_exam_summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_verdicts_case_id", "verdicts", ["case_id"], unique=True)

    op.create_table(
        "api_usage",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("endpoint", sa.String(length=120), nullable=False),
        sa.Column("usage_date", sa.String(length=10), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False),
        sa.Column("cache_hits", sa.Integer(), nullable=False),
        sa.Column("cache_misses", sa.Integer(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_usage_provider", "api_usage", ["provider"], unique=False)
    op.create_index("ix_api_usage_usage_date", "api_usage", ["usage_date"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_api_usage_usage_date", table_name="api_usage")
    op.drop_index("ix_api_usage_provider", table_name="api_usage")
    op.drop_table("api_usage")

    op.drop_index("ix_verdicts_case_id", table_name="verdicts")
    op.drop_table("verdicts")

    op.drop_index("ix_user_questions_case_id", table_name="user_questions")
    op.drop_table("user_questions")

    op.drop_index("ix_cross_examinations_case_id", table_name="cross_examinations")
    op.drop_table("cross_examinations")

    op.drop_index("ix_agent_runs_case_id", table_name="agent_runs")
    op.drop_table("agent_runs")

    op.drop_index("ix_arguments_case_id", table_name="arguments")
    op.drop_table("arguments")

    op.drop_index("ix_claims_case_id", table_name="claims")
    op.drop_table("claims")

    op.drop_index("ix_evidence_source_id", table_name="evidence")
    op.drop_index("ix_evidence_case_id", table_name="evidence")
    op.drop_table("evidence")

    op.drop_index("ix_sources_case_id", table_name="sources")
    op.drop_table("sources")

    op.drop_index("ix_cases_user_id", table_name="cases")
    op.drop_index("ix_cases_status", table_name="cases")
    op.drop_index("ix_cases_created_at", table_name="cases")
    op.drop_table("cases")

    op.drop_table("users")
