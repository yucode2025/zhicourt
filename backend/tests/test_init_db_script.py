"""The documented demo seeding command must finish a case, not only enqueue it."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path


BACKEND = Path(__file__).resolve().parent.parent


def test_init_db_seeds_completed_demo_case(tmp_path: Path) -> None:
    database = tmp_path / "demo.sqlite3"
    env = os.environ.copy()
    env.update({
        "APP_ENV": "dev",
        "DATABASE_URL": f"sqlite+pysqlite:///{database.as_posix()}",
        "LLM_BASE_URL": "",
        "LLM_API_KEY": "",
        "ZHIHU_API_BASE_URL": "",
        "ZHIHU_ACCESS_SECRET": "",
        "ZHIHU_API_KEY": "",
        "WEB_SEARCH_BASE_URL": "",
        "WEB_SEARCH_API_KEY": "",
    })
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND, env=env, check=True, capture_output=True, text=True,
    )
    for _ in range(2):
        subprocess.run(
            [sys.executable, "scripts/init_db.py"],
            cwd=BACKEND, env=env, check=True, capture_output=True, text=True,
        )
    with closing(sqlite3.connect(database)) as connection:
        rows = connection.execute(
            "SELECT status, is_public FROM cases WHERE is_demo = 1"
        ).fetchall()
    assert rows == [("verdict_ready", 1)]
