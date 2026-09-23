"""normalized evidence relations and integrity constraints

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-11
"""
from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_NAMING = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}


def _has_column(bind, table: str, column: str) -> bool:
    return column in {item["name"] for item in sa.inspect(bind).get_columns(table)}


def _has_table(bind, table: str) -> bool:
    return table in sa.inspect(bind).get_table_names()


def _has_index_or_unique(bind, table: str, name: str) -> bool:
    inspector = sa.inspect(bind)
    names = {item.get("name") for item in inspector.get_indexes(table)}
    names.update(item.get("name") for item in inspector.get_unique_constraints(table))
    return name in names


def _repair_stamped_0003_contract(bind) -> None:
    """补齐早期 create_all + stamp 产生的 0003 漂移库。"""
    if not _has_column(bind, "users", "account_kind"):
        op.add_column("users", sa.Column("account_kind", sa.String(15), nullable=True))
    bind.execute(sa.text(
        "UPDATE users SET account_kind=CASE WHEN username IS NULL AND password_hash IS NULL "
        "THEN 'anonymous' ELSE 'member' END WHERE account_kind IS NULL"
    ))
    account_kind = next(item for item in sa.inspect(bind).get_columns("users") if item["name"] == "account_kind")
    has_account_index = _has_index_or_unique(bind, "users", "ix_users_account_kind")
    if account_kind.get("nullable", True) or not has_account_index:
        with op.batch_alter_table("users", naming_convention=_NAMING) as batch:
            if account_kind.get("nullable", True):
                batch.alter_column(
                    "account_kind", existing_type=sa.String(15), nullable=False, server_default="member"
                )
            if not has_account_index:
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
    for name, columns in (
        ("ix_anonymous_sessions_user_id", ["user_id"]),
        ("ix_anonymous_sessions_expires_at", ["expires_at"]),
        ("ix_anonymous_sessions_status", ["status"]),
    ):
        if not _has_index_or_unique(bind, "anonymous_sessions", name):
            op.create_index(name, "anonymous_sessions", columns)

    if not _has_column(bind, "user_questions", "user_id"):
        op.add_column("user_questions", sa.Column("user_id", sa.String(40), nullable=True))
    if not _has_index_or_unique(bind, "user_questions", "ix_user_questions_user_id"):
        op.create_index("ix_user_questions_user_id", "user_questions", ["user_id"])

    case_public = next(item for item in sa.inspect(bind).get_columns("cases") if item["name"] == "is_public")
    if str(case_public.get("default") or "").strip("()'\"") not in {"0", "false"}:
        with op.batch_alter_table("cases", naming_convention=_NAMING) as batch:
            batch.alter_column("is_public", existing_type=sa.Boolean(), nullable=False, server_default=sa.text("0"))

    if not _has_index_or_unique(bind, "favorites", "uq_favorites_user_case"):
        duplicates = bind.execute(sa.text(
            "SELECT user_id,case_id,MIN(id) keep_id FROM favorites "
            "GROUP BY user_id,case_id HAVING COUNT(*)>1"
        )).fetchall()
        for user_id, case_id, keep_id in duplicates:
            bind.execute(
                sa.text("DELETE FROM favorites WHERE user_id=:uid AND case_id=:cid AND id<>:keep"),
                {"uid": user_id, "cid": case_id, "keep": keep_id},
            )
        with op.batch_alter_table("favorites", naming_convention=_NAMING) as batch:
            batch.create_unique_constraint("uq_favorites_user_case", ["user_id", "case_id"])

    if not _has_index_or_unique(bind, "api_usage", "uq_api_usage_provider_date"):
        groups = bind.execute(sa.text(
            "SELECT provider,usage_date,MIN(id) keep_id,SUM(COALESCE(calls,0)) calls,"
            "SUM(COALESCE(cache_hits,0)) hits,SUM(COALESCE(cache_misses,0)) misses,"
            "SUM(COALESCE(failures,0)) failures FROM api_usage "
            "GROUP BY provider,usage_date HAVING COUNT(*)>1"
        )).fetchall()
        for provider, day, keep_id, calls, hits, misses, failures in groups:
            bind.execute(sa.text(
                "UPDATE api_usage SET calls=:calls,cache_hits=:hits,cache_misses=:misses,failures=:failures "
                "WHERE id=:keep"
            ), {"calls": calls, "hits": hits, "misses": misses, "failures": failures, "keep": keep_id})
            bind.execute(sa.text(
                "DELETE FROM api_usage WHERE provider=:provider AND usage_date=:day AND id<>:keep"
            ), {"provider": provider, "day": day, "keep": keep_id})
        bind.execute(sa.text(
            "UPDATE api_usage SET calls=COALESCE(calls,0),cache_hits=COALESCE(cache_hits,0),"
            "cache_misses=COALESCE(cache_misses,0),failures=COALESCE(failures,0)"
        ))
        with op.batch_alter_table("api_usage", naming_convention=_NAMING) as batch:
            batch.create_unique_constraint("uq_api_usage_provider_date", ["provider", "usage_date"])


def _json_ids(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item for item in value if isinstance(item, str) and item))


def _json_value(ids: list[str]) -> str:
    return json.dumps(ids, ensure_ascii=False, separators=(",", ":"))


def _rows(bind, sql: str):
    return bind.execute(sa.text(sql)).mappings().all()


def _clean_and_backfill(bind) -> None:
    # 先清理 0002 中允许 NULL 的外围表，避免收紧列和 ondelete 时失败。
    bind.execute(sa.text("DELETE FROM user_sessions WHERE user_id IS NULL OR NOT EXISTS (SELECT 1 FROM users u WHERE u.id=user_sessions.user_id)"))
    bind.execute(sa.text("DELETE FROM favorites WHERE user_id IS NULL OR case_id IS NULL OR NOT EXISTS (SELECT 1 FROM users u WHERE u.id=favorites.user_id) OR NOT EXISTS (SELECT 1 FROM cases c WHERE c.id=favorites.case_id)"))
    bind.execute(sa.text("DELETE FROM anonymous_sessions WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id=anonymous_sessions.user_id)"))
    bind.execute(sa.text("UPDATE anonymous_sessions SET migrated_to_user_id=NULL WHERE migrated_to_user_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id=anonymous_sessions.migrated_to_user_id)"))
    bind.execute(sa.text("UPDATE users SET role='user' WHERE role IS NULL OR role NOT IN ('user','admin')"))
    bind.execute(sa.text("UPDATE users SET status='disabled' WHERE status IS NULL OR status NOT IN ('active','disabled')"))
    bind.execute(sa.text("UPDATE users SET account_kind=CASE WHEN username IS NULL AND password_hash IS NULL THEN 'anonymous' ELSE 'member' END WHERE account_kind IS NULL OR account_kind NOT IN ('member','anonymous')"))
    bind.execute(sa.text("UPDATE anonymous_sessions SET status='revoked' WHERE status IS NULL OR status NOT IN ('active','migrated','revoked','expired')"))
    bind.execute(sa.text("UPDATE cases SET user_id=NULL WHERE user_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id=cases.user_id)"))
    bind.execute(sa.text("UPDATE user_questions SET user_id=NULL WHERE user_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id=user_questions.user_id)"))
    bind.execute(sa.text("UPDATE user_sessions SET created_at=COALESCE(created_at,CURRENT_TIMESTAMP), expires_at=COALESCE(expires_at,CURRENT_TIMESTAMP), ip=COALESCE(ip,'')"))
    bind.execute(sa.text("UPDATE favorites SET created_at=COALESCE(created_at,CURRENT_TIMESTAMP)"))
    bind.execute(sa.text("UPDATE admin_audit_logs SET admin_user_id=COALESCE(admin_user_id,''), action=COALESCE(action,'UNKNOWN'), target=COALESCE(target,''), detail=COALESCE(detail,''), ip=COALESCE(ip,''), result=CASE WHEN result IN ('ok','denied','failed') THEN result ELSE 'failed' END, created_at=COALESCE(created_at,CURRENT_TIMESTAMP)"))
    bind.execute(sa.text("UPDATE system_settings SET updated_at=COALESCE(updated_at,CURRENT_TIMESTAMP), updated_by=COALESCE(updated_by,'')"))

    # 没有所属案件的派生产物是不可达垃圾，删除是唯一不会错误归属数据的修复。
    for table in (
        "claim_evidence", "argument_evidence", "argument_claim",
        "verdict_strongest_evidence", "verdict_counter_evidence",
        "cross_exam_evidence", "user_question_evidence",
    ):
        if _has_table(bind, table):
            bind.execute(sa.text(f"DELETE FROM {table}"))

    for table in ("agent_runs", "cross_examinations", "user_questions", "verdicts", "arguments", "claims", "evidence", "sources"):
        bind.execute(sa.text(
            f"DELETE FROM {table} WHERE case_id IS NULL OR NOT EXISTS "
            f"(SELECT 1 FROM cases WHERE cases.id={table}.case_id)"
        ))

    # Evidence 不能被猜测性地移到 Source 所在案件；缺失/跨案件来源的证据及其兼容引用一并清除。
    bad_evidence = {
        row["id"] for row in _rows(bind,
            "SELECT e.id FROM evidence e LEFT JOIN sources s ON s.id=e.source_id "
            "WHERE s.id IS NULL OR s.case_id<>e.case_id"
        )
    }
    if bad_evidence:
        affected_cases = {
            row["case_id"] for row in _rows(bind,
                "SELECT e.case_id FROM evidence e LEFT JOIN sources s ON s.id=e.source_id "
                "WHERE s.id IS NULL OR s.case_id<>e.case_id"
            )
        }
        for case_id in affected_cases:
            bind.execute(sa.text(
                "UPDATE cases SET status='failed', current_stage='failed', is_public=0, public_id=NULL, "
                "error_message='数据完整性迁移：证据来源缺失或跨案件，已阻止继续使用' WHERE id=:case_id"
            ), {"case_id": case_id})
            bind.execute(sa.text("DELETE FROM verdicts WHERE case_id=:case_id"), {"case_id": case_id})
        bind.execute(sa.text(
            "DELETE FROM evidence WHERE NOT EXISTS (SELECT 1 FROM sources s "
            "WHERE s.id=evidence.source_id AND s.case_id=evidence.case_id)"
        ))

    # 无法确定跨案件 target 的真实意图，安全地取消 target，而不是重绑。
    bind.execute(sa.text(
        "UPDATE cross_examinations SET target_argument_id=NULL WHERE target_argument_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM arguments a WHERE a.id=cross_examinations.target_argument_id "
        "AND a.case_id=cross_examinations.case_id)"
    ))

    evidence_rows = _rows(
        bind,
        "SELECT e.id,e.case_id,e.stance,s.is_demo FROM evidence e "
        "JOIN sources s ON s.id=e.source_id AND s.case_id=e.case_id",
    )
    evidence_case = {row["id"]: row["case_id"] for row in evidence_rows}
    evidence_semantics = {
        row["id"]: (row["case_id"], row["stance"], bool(row["is_demo"]))
        for row in evidence_rows
    }
    claim_case = {row["id"]: row["case_id"] for row in _rows(bind, "SELECT id, case_id FROM claims")}

    # strongest JSON 是规范关系表的写入入口；回填前先执行与运行时一致的判决语义。
    for row in _rows(
        bind,
        "SELECT id,case_id,conclusion_stance,confidence,strongest_evidence_ids,strongest_counter_evidence_ids "
        "FROM verdicts",
    ):
        stance = row["conclusion_stance"]
        support = _json_ids(row["strongest_evidence_ids"])
        counter = _json_ids(row["strongest_counter_evidence_ids"])
        expected = {"prosecution": ("pro", "con"), "defense": ("con", "pro")}.get(stance)
        if expected is None:
            support, counter = [], []
        else:
            support = [
                item for item in support
                if evidence_semantics.get(item) == (row["case_id"], expected[0], False)
            ]
            support_set = set(support)
            counter = [
                item for item in counter
                if item not in support_set
                and evidence_semantics.get(item) == (row["case_id"], expected[1], False)
            ]
            if not support or not counter:
                stance, support, counter = "insufficient", [], []
        confidence = row["confidence"]
        if stance == "insufficient" and (confidence is None or confidence > 0.2):
            confidence = 0.2
        bind.execute(
            sa.text(
                "UPDATE verdicts SET conclusion_stance=:stance,confidence=:confidence,"
                "strongest_evidence_ids=:support,strongest_counter_evidence_ids=:counter WHERE id=:id"
            ),
            {
                "id": row["id"], "stance": stance, "confidence": confidence,
                "support": _json_value(support), "counter": _json_value(counter),
            },
        )

    specs = (
        ("claims", "id", "evidence_ids", "claim_evidence", "claim_id", "evidence_id", evidence_case),
        ("arguments", "id", "evidence_ids", "argument_evidence", "argument_id", "evidence_id", evidence_case),
        ("verdicts", "id", "strongest_evidence_ids", "verdict_strongest_evidence", "verdict_id", "evidence_id", evidence_case),
        ("verdicts", "id", "strongest_counter_evidence_ids", "verdict_counter_evidence", "verdict_id", "evidence_id", evidence_case),
        ("cross_examinations", "id", "related_evidence_ids", "cross_exam_evidence", "cross_exam_id", "evidence_id", evidence_case),
        ("user_questions", "id", "related_evidence_ids", "user_question_evidence", "user_question_id", "evidence_id", evidence_case),
    )
    for owner_table, owner_pk, json_col, link_table, link_owner, link_target, targets in specs:
        for row in _rows(bind, f"SELECT {owner_pk} owner_id, case_id, {json_col} ids FROM {owner_table}"):
            valid = [item for item in _json_ids(row["ids"]) if targets.get(item) == row["case_id"]]
            bind.execute(
                sa.text(f"UPDATE {owner_table} SET {json_col}=:ids WHERE {owner_pk}=:owner_id"),
                {"ids": _json_value(valid), "owner_id": row["owner_id"]},
            )
            if valid:
                bind.execute(
                    sa.text(f"INSERT INTO {link_table} (case_id,{link_owner},{link_target}) "
                            f"VALUES (:case_id,:owner_id,:target_id)"),
                    [{"case_id": row["case_id"], "owner_id": row["owner_id"], "target_id": item} for item in valid],
                )

    # 0003 没有 Argument.claim_ids：按同案且共享 evidence 的 Claim 确定性回填。
    claims = _rows(bind, "SELECT id, case_id, evidence_ids FROM claims")
    claims_by_case: dict[str, list[tuple[str, set[str]]]] = {}
    for row in claims:
        claims_by_case.setdefault(row["case_id"], []).append((row["id"], set(_json_ids(row["evidence_ids"]))))
    for row in _rows(bind, "SELECT id, case_id, evidence_ids FROM arguments"):
        arg_evidence = set(_json_ids(row["evidence_ids"]))
        claim_ids = [claim_id for claim_id, claim_evidence in claims_by_case.get(row["case_id"], []) if arg_evidence & claim_evidence]
        bind.execute(sa.text("UPDATE arguments SET claim_ids=:ids WHERE id=:id"), {"ids": _json_value(claim_ids), "id": row["id"]})
        if claim_ids:
            bind.execute(
                sa.text("INSERT INTO argument_claim (case_id,argument_id,claim_id) VALUES (:case_id,:argument_id,:claim_id)"),
                [{"case_id": row["case_id"], "argument_id": row["id"], "claim_id": item} for item in claim_ids],
            )

    # 数值脏数据采取保守截断；枚举脏数据降级为中立/失败，绝不提升可信度或公开性。
    bind.execute(sa.text("UPDATE sources SET vote_count=CASE WHEN vote_count IS NULL OR vote_count<0 THEN 0 ELSE vote_count END, comment_count=CASE WHEN comment_count IS NULL OR comment_count<0 THEN 0 ELSE comment_count END"))
    for column, default in (("relevance_score", 0), ("authority_score", 0), ("community_score", 0), ("recency_score", 0), ("independence_score", 1), ("rank_score", 0)):
        bind.execute(sa.text(f"UPDATE sources SET {column}=CASE WHEN {column} IS NULL THEN {default} WHEN {column}<0 THEN 0 WHEN {column}>1 THEN 1 ELSE {column} END"))
    bind.execute(sa.text("UPDATE sources SET authority_level=NULL WHERE authority_level IS NOT NULL AND (authority_level<1 OR authority_level>5)"))
    bind.execute(sa.text("UPDATE sources SET origin='demo', is_demo=1 WHERE origin IS NULL OR origin NOT IN ('zhihu','web','demo')"))

    bind.execute(sa.text("UPDATE evidence SET stance='neutral' WHERE stance IS NULL OR stance NOT IN ('pro','con','neutral')"))
    bind.execute(sa.text("UPDATE evidence SET evidence_type='opinion' WHERE evidence_type IS NULL OR evidence_type NOT IN ('fact','opinion','data','case','prediction','assumption','limitation')"))
    for column in ("relevance_score", "authority_score", "strength"):
        bind.execute(sa.text(f"UPDATE evidence SET {column}=CASE WHEN {column} IS NULL OR {column}<0 THEN 0 WHEN {column}>1 THEN 1 ELSE {column} END"))
    bind.execute(sa.text("UPDATE claims SET side='court' WHERE side IS NULL OR side NOT IN ('pro','con','court')"))
    bind.execute(sa.text("UPDATE arguments SET side='court' WHERE side IS NULL OR side NOT IN ('prosecution','defense','court')"))
    bind.execute(sa.text("UPDATE arguments SET strength=CASE WHEN strength IS NULL OR strength<0 THEN 0 WHEN strength>1 THEN 1 ELSE strength END"))
    bind.execute(sa.text("UPDATE cross_examinations SET target_side='both' WHERE target_side IS NULL OR target_side NOT IN ('prosecution','defense','both')"))
    bind.execute(sa.text("UPDATE cross_examinations SET severity='medium' WHERE severity IS NULL OR severity NOT IN ('low','medium','high')"))
    bind.execute(sa.text("UPDATE user_questions SET target='judge' WHERE target IS NULL OR target NOT IN ('prosecution','defense','judge','evidence','source')"))
    bind.execute(sa.text("UPDATE user_questions SET challenge_type='other' WHERE challenge_type IS NULL OR challenge_type NOT IN ('fact','logic','evidence','definition','scope','source','other')"))
    bind.execute(sa.text("UPDATE verdicts SET conclusion_stance='insufficient' WHERE conclusion_stance IS NULL OR conclusion_stance NOT IN ('prosecution','defense','conditional','insufficient')"))
    bind.execute(sa.text("UPDATE verdicts SET confidence=CASE WHEN confidence IS NULL OR confidence<0 THEN 0 WHEN confidence>1 THEN 1 ELSE confidence END"))
    bind.execute(sa.text("UPDATE agent_runs SET status='failed' WHERE status IS NULL OR status NOT IN ('running','done','failed','skipped')"))
    bind.execute(sa.text("UPDATE agent_runs SET mode='heuristic' WHERE mode IS NULL OR mode NOT IN ('llm','heuristic','provider','mixed')"))
    bind.execute(sa.text("UPDATE agent_runs SET token_usage=CASE WHEN token_usage IS NULL OR token_usage<0 THEN 0 ELSE token_usage END, duration_ms=CASE WHEN duration_ms IS NULL OR duration_ms<0 THEN 0 ELSE duration_ms END"))
    bind.execute(sa.text("UPDATE api_usage SET calls=CASE WHEN calls IS NULL OR calls<0 THEN 0 ELSE calls END, cache_hits=CASE WHEN cache_hits IS NULL OR cache_hits<0 THEN 0 ELSE cache_hits END, cache_misses=CASE WHEN cache_misses IS NULL OR cache_misses<0 THEN 0 ELSE cache_misses END, failures=CASE WHEN failures IS NULL OR failures<0 THEN 0 ELSE failures END"))

    bind.execute(sa.text("UPDATE cases SET status='failed', current_stage='failed', error_message='数据完整性迁移：非法案件状态' WHERE status IS NULL OR status NOT IN ('created','queued','running','verdict_ready','failed')"))
    bind.execute(sa.text("UPDATE cases SET engine_mode='heuristic' WHERE engine_mode IS NULL OR engine_mode NOT IN ('heuristic','llm','mixed')"))
    bind.execute(sa.text("UPDATE cases SET progress_index=0 WHERE progress_index IS NULL OR progress_index<0"))
    bind.execute(sa.text(
        "UPDATE cases SET is_public=0, public_id=NULL WHERE is_public=1 AND "
        "(status<>'verdict_ready' OR public_id IS NULL OR NOT EXISTS "
        "(SELECT 1 FROM verdicts v WHERE v.case_id=cases.id))"
    ))
    # 状态与判决互相矛盾时不猜测哪一方正确：撤销公开、标记失败，并移除未完成案件的判决。
    bind.execute(sa.text(
        "UPDATE cases SET status='failed', current_stage='failed', is_public=0, public_id=NULL, "
        "error_message='数据完整性迁移：未完成案件存在判决，已阻止继续使用' "
        "WHERE status<>'verdict_ready' AND EXISTS (SELECT 1 FROM verdicts v WHERE v.case_id=cases.id)"
    ))
    bind.execute(sa.text("DELETE FROM verdicts WHERE EXISTS (SELECT 1 FROM cases c WHERE c.id=verdicts.case_id AND c.status='failed' AND c.error_message='数据完整性迁移：未完成案件存在判决，已阻止继续使用')"))
    bind.execute(sa.text(
        "UPDATE cases SET status='failed', current_stage='failed', is_public=0, public_id=NULL, "
        "error_message='数据完整性迁移：完成案件缺少判决，已阻止继续使用' "
        "WHERE status='verdict_ready' AND NOT EXISTS (SELECT 1 FROM verdicts v WHERE v.case_id=cases.id)"
    ))
    # 没有任何真实来源的案件只能生成不足判决；清空强证据引用，避免 Mock 被当作现实依据。
    bind.execute(sa.text(
        "UPDATE cases SET is_demo=1 WHERE EXISTS (SELECT 1 FROM sources s WHERE s.case_id=cases.id) "
        "AND NOT EXISTS (SELECT 1 FROM sources s WHERE s.case_id=cases.id AND s.is_demo=0)"
    ))
    bind.execute(sa.text(
        "UPDATE verdicts SET conclusion_stance='insufficient', confidence=CASE WHEN confidence>0.15 THEN 0.15 ELSE confidence END, "
        "strongest_evidence_ids='[]', strongest_counter_evidence_ids='[]' "
        "WHERE NOT EXISTS (SELECT 1 FROM sources s WHERE s.case_id=verdicts.case_id AND s.is_demo=0)"
    ))
    bind.execute(sa.text(
        "DELETE FROM verdict_strongest_evidence WHERE NOT EXISTS "
        "(SELECT 1 FROM sources s WHERE s.case_id=verdict_strongest_evidence.case_id AND s.is_demo=0)"
    ))
    bind.execute(sa.text(
        "DELETE FROM verdict_counter_evidence WHERE NOT EXISTS "
        "(SELECT 1 FROM sources s WHERE s.case_id=verdict_counter_evidence.case_id AND s.is_demo=0)"
    ))

    # 主体文本缺失无法可信重建：案件 fail closed；输出文本缺失则用明确占位防止伪造内容。
    broken_cases = _rows(bind, "SELECT id FROM cases WHERE title IS NULL OR original_question IS NULL OR proposition IS NULL")
    if broken_cases:
        bind.execute(sa.text(
            "UPDATE cases SET title=COALESCE(title,'数据不完整案件'), original_question=COALESCE(original_question,''), "
            "proposition=COALESCE(proposition,''), status='failed', current_stage='failed', is_public=0, public_id=NULL, "
            "error_message='数据完整性迁移：案件核心文本缺失，已阻止继续使用' "
            "WHERE title IS NULL OR original_question IS NULL OR proposition IS NULL"
        ))


def _replace_fk(
    bind, table: str, columns: list[str], target: str, remote_columns: list[str],
    name: str, ondelete: str,
) -> None:
    matches = [
        fk for fk in sa.inspect(bind).get_foreign_keys(table)
        if fk.get("constrained_columns") == columns
        or len(columns) > 1 and fk.get("constrained_columns") == [columns[0]]
    ]
    with op.batch_alter_table(table, naming_convention=_NAMING) as batch:
        for fk in matches:
            fallback = f"fk_{table}_{columns[0]}_{fk['referred_table']}"
            batch.drop_constraint(fk.get("name") or fallback, type_="foreignkey")
        batch.create_foreign_key(name, target, columns, remote_columns, ondelete=ondelete)


def _create_link_table(name: str, owner_table: str, owner_column: str, target_table: str, target_column: str) -> None:
    op.create_table(
        name,
        sa.Column("case_id", sa.String(40), nullable=False),
        sa.Column(owner_column, sa.String(40), nullable=False),
        sa.Column(target_column, sa.String(40), nullable=False),
        sa.ForeignKeyConstraint([owner_column, "case_id"], [f"{owner_table}.id", f"{owner_table}.case_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint([target_column, "case_id"], [f"{target_table}.id", f"{target_table}.case_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("case_id", owner_column, target_column),
    )


def upgrade() -> None:
    bind = op.get_bind()
    _repair_stamped_0003_contract(bind)
    if not _has_column(bind, "sources", "authority_level"):
        op.add_column("sources", sa.Column("authority_level", sa.Integer(), nullable=True))
    if not _has_column(bind, "arguments", "claim_ids"):
        op.add_column("arguments", sa.Column("claim_ids", sa.JSON(), nullable=True))
        bind.execute(sa.text("UPDATE arguments SET claim_ids='[]' WHERE claim_ids IS NULL"))
        with op.batch_alter_table("arguments", naming_convention=_NAMING) as batch:
            batch.alter_column("claim_ids", existing_type=sa.JSON(), nullable=False)

    # 复合 FK 的被引用列必须显式唯一（MariaDB 要求索引，SQLite 要求唯一性）。
    unique_specs = (
        ("sources", "uq_sources_id_case"), ("evidence", "uq_evidence_id_case"),
        ("claims", "uq_claims_id_case"), ("arguments", "uq_arguments_id_case"),
        ("verdicts", "uq_verdicts_id_case"), ("cross_examinations", "uq_cross_examinations_id_case"),
        ("user_questions", "uq_user_questions_id_case"),
    )
    for table, name in unique_specs:
        existing = {u.get("name") for u in sa.inspect(bind).get_unique_constraints(table)}
        if name not in existing:
            with op.batch_alter_table(table, naming_convention=_NAMING) as batch:
                batch.create_unique_constraint(name, ["id", "case_id"])

    link_specs = (
        ("claim_evidence", "claims", "claim_id", "evidence", "evidence_id"),
        ("argument_evidence", "arguments", "argument_id", "evidence", "evidence_id"),
        ("argument_claim", "arguments", "argument_id", "claims", "claim_id"),
        ("verdict_strongest_evidence", "verdicts", "verdict_id", "evidence", "evidence_id"),
        ("verdict_counter_evidence", "verdicts", "verdict_id", "evidence", "evidence_id"),
        ("cross_exam_evidence", "cross_examinations", "cross_exam_id", "evidence", "evidence_id"),
        ("user_question_evidence", "user_questions", "user_question_id", "evidence", "evidence_id"),
    )
    for spec in link_specs:
        if not _has_table(bind, spec[0]):
            _create_link_table(*spec)

    _clean_and_backfill(bind)

    # 0002 的新表曾留下隐式 nullable 列，清理后收紧为应用契约要求的 NOT NULL。
    not_null_specs = {
        "user_sessions": (("user_id", sa.String(40)), ("created_at", sa.DateTime()), ("expires_at", sa.DateTime()), ("ip", sa.String(45))),
        "favorites": (("user_id", sa.String(40)), ("case_id", sa.String(40)), ("created_at", sa.DateTime())),
        "admin_audit_logs": (("admin_user_id", sa.String(40)), ("action", sa.String(50)), ("target", sa.String(120)), ("detail", sa.String(500)), ("ip", sa.String(45)), ("result", sa.String(10)), ("created_at", sa.DateTime())),
        "system_settings": (("updated_at", sa.DateTime()), ("updated_by", sa.String(40))),
    }
    for table, columns in not_null_specs.items():
        current = {item["name"]: item for item in sa.inspect(bind).get_columns(table)}
        if any(current[name].get("nullable", True) for name, _type in columns):
            with op.batch_alter_table(table, naming_convention=_NAMING) as batch:
                for name, column_type in columns:
                    if current[name].get("nullable", True):
                        batch.alter_column(name, existing_type=column_type, nullable=False)

    # 明确生命周期；案件删除级联派生产物，用户删除仅解除案件/质询归属。
    _replace_fk(bind, "user_sessions", ["user_id"], "users", ["id"], "fk_user_sessions_user_id", "CASCADE")
    _replace_fk(bind, "anonymous_sessions", ["user_id"], "users", ["id"], "fk_anonymous_sessions_user_id", "CASCADE")
    _replace_fk(bind, "anonymous_sessions", ["migrated_to_user_id"], "users", ["id"], "fk_anonymous_sessions_migrated_user_id", "SET NULL")
    _replace_fk(bind, "favorites", ["user_id"], "users", ["id"], "fk_favorites_user_id", "CASCADE")
    _replace_fk(bind, "favorites", ["case_id"], "cases", ["id"], "fk_favorites_case_id", "CASCADE")
    _replace_fk(bind, "cases", ["user_id"], "users", ["id"], "fk_cases_user_id", "SET NULL")
    _replace_fk(bind, "user_questions", ["user_id"], "users", ["id"], "fk_user_questions_user_id", "SET NULL")
    for table in ("sources", "evidence", "claims", "arguments", "agent_runs", "cross_examinations", "user_questions", "verdicts"):
        _replace_fk(bind, table, ["case_id"], "cases", ["id"], f"fk_{table}_case_id", "CASCADE")

    # Evidence.source 使用复合 FK，数据库层保证与 Evidence 同案。
    _replace_fk(bind, "evidence", ["source_id", "case_id"], "sources", ["id", "case_id"], "fk_evidence_source_case", "CASCADE")

    # target_argument 同样必须同案；非法旧值已在上方置空。
    _replace_fk(bind, "cross_examinations", ["target_argument_id", "case_id"], "arguments", ["id", "case_id"], "fk_cross_exam_target_argument_case", "CASCADE")

    checks: dict[str, list[tuple[str, str]]] = {
        "users": [("ck_users_role", "role IN ('user','admin')"), ("ck_users_status", "status IN ('active','disabled')"), ("ck_users_account_kind", "account_kind IN ('member','anonymous')")],
        "anonymous_sessions": [("ck_anonymous_sessions_status", "status IN ('active','migrated','revoked','expired')")],
        "admin_audit_logs": [("ck_admin_audit_logs_result", "result IN ('ok','denied','failed')")],
        "cases": [("ck_cases_status", "status IN ('created','queued','running','verdict_ready','failed')"), ("ck_cases_engine_mode", "engine_mode IN ('heuristic','llm','mixed')"), ("ck_cases_progress_index", "progress_index >= 0")],
        "sources": [("ck_sources_origin", "origin IN ('zhihu','web','demo')"), ("ck_sources_counts", "vote_count >= 0 AND comment_count >= 0"), ("ck_sources_relevance_score", "relevance_score BETWEEN 0 AND 1"), ("ck_sources_authority_score", "authority_score BETWEEN 0 AND 1"), ("ck_sources_community_score", "community_score BETWEEN 0 AND 1"), ("ck_sources_recency_score", "recency_score BETWEEN 0 AND 1"), ("ck_sources_independence_score", "independence_score BETWEEN 0 AND 1"), ("ck_sources_rank_score", "rank_score BETWEEN 0 AND 1"), ("ck_sources_authority_level", "authority_level IS NULL OR authority_level BETWEEN 1 AND 5")],
        "evidence": [("ck_evidence_stance", "stance IN ('pro','con','neutral')"), ("ck_evidence_type", "evidence_type IN ('fact','opinion','data','case','prediction','assumption','limitation')"), ("ck_evidence_relevance_score", "relevance_score BETWEEN 0 AND 1"), ("ck_evidence_authority_score", "authority_score BETWEEN 0 AND 1"), ("ck_evidence_strength", "strength BETWEEN 0 AND 1")],
        "claims": [("ck_claims_side", "side IN ('pro','con','court')")],
        "arguments": [("ck_arguments_side", "side IN ('prosecution','defense','court')"), ("ck_arguments_strength", "strength BETWEEN 0 AND 1")],
        "agent_runs": [("ck_agent_runs_status", "status IN ('running','done','failed','skipped')"), ("ck_agent_runs_mode", "mode IN ('llm','heuristic','provider','mixed')"), ("ck_agent_runs_usage", "token_usage >= 0 AND duration_ms >= 0")],
        "cross_examinations": [("ck_cross_exam_target_side", "target_side IN ('prosecution','defense','both')"), ("ck_cross_exam_severity", "severity IN ('low','medium','high')")],
        "user_questions": [("ck_user_questions_target", "target IN ('prosecution','defense','judge','evidence','source')"), ("ck_user_questions_challenge_type", "challenge_type IN ('fact','logic','evidence','definition','scope','source','other')")],
        "verdicts": [("ck_verdicts_stance", "conclusion_stance IN ('prosecution','defense','conditional','insufficient')"), ("ck_verdicts_confidence", "confidence BETWEEN 0 AND 1")],
        "api_usage": [("ck_api_usage_counts", "calls >= 0 AND cache_hits >= 0 AND cache_misses >= 0 AND failures >= 0")],
    }
    for table, table_checks in checks.items():
        existing = {item.get("name") for item in sa.inspect(bind).get_check_constraints(table)}
        missing = [(name, condition) for name, condition in table_checks if name not in existing]
        if missing:
            with op.batch_alter_table(table, naming_convention=_NAMING) as batch:
                for name, condition in missing:
                    batch.create_check_constraint(name, condition)


def downgrade() -> None:
    raise RuntimeError("0004 normalizes and removes unsafe references; restore a verified pre-upgrade backup instead")
