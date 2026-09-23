"""0004 空库迁移、旧 JSON 回填与安全清理。"""
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


def _alembic(database: Path, revision: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+pysqlite:///{database.as_posix()}"
    subprocess.run(
        [str(PYTHON), "-m", "alembic", "-c", str(BACKEND / "alembic.ini"), "upgrade", revision],
        cwd=BACKEND,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _stamp(database: Path, revision: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+pysqlite:///{database.as_posix()}"
    subprocess.run(
        [str(PYTHON), "-m", "alembic", "-c", str(BACKEND / "alembic.ini"), "stamp", revision],
        cwd=BACKEND,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_empty_database_migrates_to_head(tmp_path):
    database = tmp_path / "empty.sqlite3"
    _alembic(database, "head")
    with closing(sqlite3.connect(database)) as connection:
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        assert version >= "0005"  # head 会随后续迁移前进（0006/0007…）
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {
            "claim_evidence", "argument_evidence", "argument_claim",
            "verdict_strongest_evidence", "verdict_counter_evidence",
            "cross_exam_evidence", "user_question_evidence",
            "zhihu_oauth_states", "zhihu_oauth_accounts",
        } <= tables
        assert connection.execute("PRAGMA table_info(arguments)").fetchall()
        assert "claim_ids" in {row[1] for row in connection.execute("PRAGMA table_info(arguments)")}
        assert "authority_level" in {row[1] for row in connection.execute("PRAGMA table_info(sources)")}


def test_head_repairs_legacy_database_stamped_0003_without_account_kind(tmp_path):
    database = tmp_path / "legacy-stamped.sqlite3"
    _alembic(database, "0002")
    _stamp(database, "0003")

    _alembic(database, "head")
    with closing(sqlite3.connect(database)) as connection:
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        assert version >= "0005"
        columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
        assert "account_kind" in columns
        assert "anonymous_sessions" in {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "user_id" in {row[1] for row in connection.execute("PRAGMA table_info(user_questions)")}


def test_0004_backfills_relations_and_cleans_unsafe_data(tmp_path):
    database = tmp_path / "upgrade.sqlite3"
    _alembic(database, "0003")
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.executemany(
            "INSERT INTO cases (id,user_id,title,original_question,proposition,status,current_stage,plan,progress_events,progress_index,error_message,engine_mode,is_demo,is_public,public_id,finished_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("case_a", None, "a", "q", "p", "verdict_ready", "done", None, "[]", 1, None, "heuristic", 0, 1, "pub-a", None, "2026-01-01", "2026-01-01"),
                ("case_b", None, "b", "q", "p", "created", "", None, "[]", 0, None, "heuristic", 0, 1, None, None, "2026-01-01", "2026-01-01"),
                ("case_c", None, "c", "q", "p", "verdict_ready", "done", None, "[]", 1, None, "heuristic", 0, 0, None, None, "2026-01-01", "2026-01-01"),
            ],
        )
        source_values = (
            "id,case_id,origin,kind,title,url,author,summary,published_at,vote_count,comment_count,"
            "relevance_score,authority_score,community_score,recency_score,independence_score,rank_score,is_demo,created_at"
        )
        connection.executemany(
            f"INSERT INTO sources ({source_values}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("src_a", "case_a", "web", "page", "a", "https://a.example", "", "", "", -4, -2, 2, -1, 0, 0, 1, 0.5, 0, "2026-01-01"),
                ("src_b", "case_b", "web", "page", "b", "https://b.example", "", "", "", 0, 0, 0, 0, 0, 0, 1, 0, 0, "2026-01-01"),
                ("src_c_pro", "case_c", "web", "page", "c pro", "https://c-pro.example", "", "", "", 0, 0, 0, 0, 0, 0, 1, 0, 0, "2026-01-01"),
                ("src_c_con", "case_c", "web", "page", "c con", "https://c-con.example", "", "", "", 0, 0, 0, 0, 0, 0, 1, 0, 0, "2026-01-01"),
            ],
        )
        evidence_values = (
            "id,case_id,source_id,claim,stance,evidence_type,summary,quoted_fragment,relevance_score,"
            "authority_score,strength,limitations,created_at"
        )
        connection.executemany(
            f"INSERT INTO evidence ({evidence_values}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("ev_a", "case_a", "src_a", "valid", "pro", "fact", "", None, 0.5, 0.5, 0.7, "[]", "2026-01-01"),
                ("ev_cross", "case_a", "src_b", "cross", "bad", "bad", "", None, 0, 0, 0, "[]", "2026-01-01"),
                ("ev_b", "case_b", "src_b", "other", "con", "opinion", "", None, 0, 0, 0, "[]", "2026-01-01"),
                ("ev_c_pro", "case_c", "src_c_pro", "support", "pro", "fact", "", None, 0.5, 0.5, 0.8, "[]", "2026-01-01"),
                ("ev_c_con", "case_c", "src_c_con", "counter", "con", "fact", "", None, 0.5, 0.5, 0.7, "[]", "2026-01-01"),
            ],
        )
        connection.execute(
            "INSERT INTO claims (id,case_id,side,text,evidence_ids,created_at) VALUES (?,?,?,?,?,?)",
            ("clm_a", "case_a", "pro", "claim", '["ev_a","ev_cross","ev_b","missing"]', "2026-01-01"),
        )
        connection.execute(
            "INSERT INTO arguments (id,case_id,side,title,body,evidence_ids,strength,created_at) VALUES (?,?,?,?,?,?,?,?)",
            ("arg_a", "case_a", "prosecution", "argument", "body", '["ev_a","ev_b"]', 0.8, "2026-01-01"),
        )
        verdict_values = (
            "id,case_id,conclusion,conclusion_stance,confidence,prosecution_summary,defense_summary,shared_facts,"
            "core_disputes,strongest_evidence_ids,strongest_counter_evidence_ids,evidence_gaps,definition_conflicts,"
            "unknowns,verdict_changers,next_questions,cross_exam_summary,created_at"
        )
        connection.executemany(
            f"INSERT INTO verdicts ({verdict_values}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("vd_a", "case_a", "result", "prosecution", 0.8, "", "", "[]", "[]", '["ev_a","ev_b"]', "[]", "[]", "[]", "[]", "[]", "[]", "", "2026-01-01"),
                ("vd_c", "case_c", "result", "conditional", 0.5, "", "", "[]", "[]", '["ev_c_pro"]', '["ev_c_con"]', "[]", "[]", "[]", "[]", "[]", "", "2026-01-01"),
            ],
        )
        connection.commit()

    _alembic(database, "head")
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        assert connection.execute("SELECT evidence_ids FROM claims WHERE id='clm_a'").fetchone()[0] == '["ev_a"]'
        assert connection.execute("SELECT evidence_ids,claim_ids FROM arguments WHERE id='arg_a'").fetchone() == ('["ev_a"]', '["clm_a"]')
        assert connection.execute("SELECT case_id,claim_id,evidence_id FROM claim_evidence").fetchall() == [("case_a", "clm_a", "ev_a")]
        assert connection.execute("SELECT case_id,argument_id,evidence_id FROM argument_evidence").fetchall() == [("case_a", "arg_a", "ev_a")]
        assert connection.execute("SELECT case_id,argument_id,claim_id FROM argument_claim").fetchall() == [("case_a", "arg_a", "clm_a")]
        assert connection.execute(
            "SELECT conclusion_stance,strongest_evidence_ids,strongest_counter_evidence_ids "
            "FROM verdicts WHERE id='vd_c'"
        ).fetchone() == ("conditional", "[]", "[]")
        assert connection.execute("SELECT COUNT(*) FROM verdict_strongest_evidence").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM verdict_counter_evidence").fetchone()[0] == 0
        assert connection.execute("SELECT id FROM evidence WHERE id='ev_cross'").fetchone() is None
        assert connection.execute("SELECT vote_count,comment_count,relevance_score,authority_score FROM sources WHERE id='src_a'").fetchone() == (0, 0, 1.0, 0.0)
        assert connection.execute("SELECT is_public,public_id FROM cases WHERE id='case_b'").fetchone() == (0, None)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE arguments SET strength=-1 WHERE id='arg_a'")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO evidence (id,case_id,source_id,claim,stance,evidence_type,summary,relevance_score,authority_score,strength,limitations,created_at) VALUES ('bad','case_a','src_b','bad','neutral','opinion','',0,0,0,'[]','2026-01-01')"
            )
