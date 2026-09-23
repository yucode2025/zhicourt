"""0007/0008/0009 执行 fencing 迁移：升级回填、修复字段、幂等与 downgrade 拒绝。

从 0005 升级（链路上包含并行批次的 0006 oauth state hardening），
验证 cases 新列、case_run_attempts 表与遗留 running/queued 的安全策略。
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
PYTHON = Path(sys.executable)

CASE_COLUMNS = {
    "run_generation", "lease_token", "lease_owner", "lease_expires_at",
    "lease_heartbeat_at", "run_started_at", "resume_from_stage",
    "last_completed_stage", "failure_stage", "failure_kind", "failure_code",
    "retry_count",
}

AGENT_RUN_COLUMNS = {
    "run_generation", "stage", "attempt", "reused_from_generation",
    "error_code", "error_kind",
}


def _alembic(database: Path, *args: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+pysqlite:///{database.as_posix()}"
    subprocess.run(
        [str(PYTHON), "-m", "alembic", "-c", str(BACKEND / "alembic.ini"), *args],
        cwd=BACKEND,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _stamp(database: Path, revision: str) -> None:
    _alembic(database, "stamp", revision)


def _case_row_sql() -> str:
    return (
        "INSERT INTO cases (id,user_id,title,original_question,proposition,status,current_stage,plan,"
        "progress_events,progress_index,error_message,engine_mode,is_demo,is_public,public_id,"
        "finished_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    )


def test_empty_database_migrates_to_head_with_fencing_schema(tmp_path):
    database = tmp_path / "empty.sqlite3"
    _alembic(database, "upgrade", "head")
    with closing(sqlite3.connect(database)) as connection:
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        assert version == "0009"
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "case_run_attempts" in tables
        columns = {row[1] for row in connection.execute("PRAGMA table_info(cases)")}
        assert CASE_COLUMNS <= columns
        assert "attempt" in {row[1] for row in connection.execute("PRAGMA table_info(case_run_attempts)")}
        agent_run_columns = {row[1] for row in connection.execute("PRAGMA table_info(agent_runs)")}
        assert AGENT_RUN_COLUMNS <= agent_run_columns
        agent_run_ddl = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='agent_runs'"
        ).fetchone()[0]
        assert "'reused'" in agent_run_ddl
        indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list('cases')")
        }
        assert "ix_cases_status_generation" in indexes
        # 约束生效
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO cases (id,title,original_question,proposition,run_generation,status)"
                " VALUES ('bad_gen','t','q','p',-1,'created')"
            )


def test_upgrade_from_0005_backfills_generation_and_interrupts_legacy_runs(tmp_path):
    database = tmp_path / "upgrade.sqlite3"
    _alembic(database, "upgrade", "0005")
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO users (id,created_at,account_kind,role,status) VALUES (?,?,?,?,?)",
            ("usr_oauth", "2026-01-01", "zhihu", "user", "active"),
        )
        connection.execute(
            "INSERT INTO zhihu_oauth_accounts "
            "(id,user_id,zhihu_uid,zhihu_hash_id,access_token,token_expires_at,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("zho_legacy", "usr_oauth", "123456789", "hash", "legacy-secret-token", "2027-01-01", "2026-01-01", "2026-01-01"),
        )
        connection.execute(
            _case_row_sql(),
            ("case_running", None, "r", "q", "p", "running", "debate", None, "[]", 3, None, "llm", 0, 0, None, None, "2026-01-01", "2026-01-01"),
        )
        connection.execute(
            _case_row_sql(),
            ("case_queued", None, "q", "q", "p", "queued", "queue", None, "[]", 0, None, "heuristic", 0, 0, None, None, "2026-01-01", "2026-01-01"),
        )
        connection.execute(
            _case_row_sql(),
            ("case_done", None, "d", "q", "p", "verdict_ready", "done", None, "[]", 8, None, "mixed", 0, 0, None, None, "2026-01-01", "2026-01-01"),
        )
        connection.commit()

    _alembic(database, "upgrade", "head")
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0009"
        oauth_state_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(zhihu_oauth_states)")
        }
        assert {
            "purpose", "initiating_user_id", "initiating_session_hash", "anonymous_session_hash",
        } <= oauth_state_columns
        assert connection.execute(
            "SELECT access_token, token_expires_at FROM zhihu_oauth_accounts WHERE id='zho_legacy'"
        ).fetchone() == ("", None)
        # 回填 generation 0
        for case_id in ("case_running", "case_queued", "case_done"):
            row = connection.execute(
                "SELECT run_generation, retry_count, lease_token, lease_owner FROM cases WHERE id=?", (case_id,)
            ).fetchone()
            assert row == (0, 0, "", ""), case_id
        # 遗留 running / queued → interrupted-failed；已完成案件不动
        assert connection.execute(
            "SELECT status, failure_kind, current_stage FROM cases WHERE id='case_running'"
        ).fetchone() == ("failed", "interrupted", "failed")
        assert connection.execute(
            "SELECT status, failure_kind FROM cases WHERE id='case_queued'"
        ).fetchone() == ("failed", "interrupted")
        assert connection.execute(
            "SELECT status FROM cases WHERE id='case_done'"
        ).fetchone() == ("verdict_ready",)


def test_upgrade_is_idempotent(tmp_path):
    database = tmp_path / "idempotent.sqlite3"
    _alembic(database, "upgrade", "0005")
    _alembic(database, "upgrade", "head")
    # 重复执行 upgrade head 不应报错、不产生副作用
    _alembic(database, "upgrade", "head")
    with closing(sqlite3.connect(database)) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(cases)")}
        assert CASE_COLUMNS <= columns
        agent_run_columns = {row[1] for row in connection.execute("PRAGMA table_info(agent_runs)")}
        assert AGENT_RUN_COLUMNS <= agent_run_columns
        assert connection.execute("SELECT COUNT(*) FROM case_run_attempts").fetchone()[0] == 0


def test_downgrade_is_rejected(tmp_path):
    database = tmp_path / "downgrade.sqlite3"
    _alembic(database, "upgrade", "head")
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+pysqlite:///{database.as_posix()}"
    result = subprocess.run(
        [str(PYTHON), "-m", "alembic", "-c", str(BACKEND / "alembic.ini"), "downgrade", "0006"],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "restore a verified pre-upgrade backup" in (result.stderr + result.stdout)
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0009"


def test_stamp_0005_repairs_head_on_legacy_database(tmp_path):
    database = tmp_path / "stamped.sqlite3"
    _alembic(database, "upgrade", "0005")
    _stamp(database, "0005")
    _alembic(database, "upgrade", "head")
    with closing(sqlite3.connect(database)) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(cases)")}
        assert CASE_COLUMNS <= columns
        agent_run_columns = {row[1] for row in connection.execute("PRAGMA table_info(agent_runs)")}
        assert AGENT_RUN_COLUMNS <= agent_run_columns


def test_upgrade_from_0007_repairs_agent_run_columns(tmp_path):
    database = tmp_path / "repair.sqlite3"
    _alembic(database, "upgrade", "0007")
    with closing(sqlite3.connect(database)) as connection:
        before = {row[1] for row in connection.execute("PRAGMA table_info(agent_runs)")}
        assert not AGENT_RUN_COLUMNS <= before

    _alembic(database, "upgrade", "head")
    with closing(sqlite3.connect(database)) as connection:
        after = {row[1] for row in connection.execute("PRAGMA table_info(agent_runs)")}
        assert AGENT_RUN_COLUMNS <= after
        indexes = {row[1] for row in connection.execute("PRAGMA index_list('agent_runs')")}
        assert "ix_agent_runs_case_generation" in indexes


def test_upgrade_from_0008_allows_reused_agent_run_status(tmp_path):
    database = tmp_path / "reused-status.sqlite3"
    _alembic(database, "upgrade", "0008")
    with closing(sqlite3.connect(database)) as connection:
        before = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='agent_runs'"
        ).fetchone()[0]
        assert "'reused'" not in before

    _alembic(database, "upgrade", "head")
    with closing(sqlite3.connect(database)) as connection:
        after = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='agent_runs'"
        ).fetchone()[0]
        assert "'reused'" in after
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0009"
