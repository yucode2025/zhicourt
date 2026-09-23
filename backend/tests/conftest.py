"""后端测试。运行方式：cd backend && .venv/Scripts/python -m pytest tests/ -v

使用 SQLite 内存库 + Mock Provider + 启发式引擎（不依赖外部服务），
与生产 MySQL 代码路径共享同一套 ORM 模型。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 测试环境：强制使用 SQLite，避免依赖 MySQL（必须在导入 app 前设置）
os.environ["DATABASE_URL"] = "sqlite+pysqlite://"

import pytest  # noqa: E402
from alembic.script import ScriptDirectory  # noqa: E402
from sqlalchemy import create_engine, event, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.models import Base  # noqa: E402

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_MIGRATION_HEAD = ScriptDirectory(str(_BACKEND_DIR / "migrations")).get_current_head()
assert _MIGRATION_HEAD is not None

# ---- 内存数据库 ----
_test_engine = create_engine(
    "sqlite+pysqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(_test_engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


TestSession = sessionmaker(bind=_test_engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def db_session():
    Base.metadata.create_all(_test_engine)
    with _test_engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE IF NOT EXISTS alembic_version "
            "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
        ))
        connection.execute(text("DELETE FROM alembic_version"))
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:head)"),
            {"head": _MIGRATION_HEAD},
        )
    session = TestSession()
    try:
        yield session
    finally:
        try:
            session.close()
            Base.metadata.drop_all(_test_engine)
            with _test_engine.begin() as connection:
                connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        finally:
            # StaticPool 持有唯一的 SQLite 内存连接；即使其他清理失败也显式释放，
            # 避免连接由解释器垃圾回收时才关闭并触发 unclosed database warning。
            _test_engine.dispose()


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """每个测试重置进程内限流/用量计数，避免测试间相互影响。"""
    from app.services import api_usage

    api_usage._MEM.clear()
    from app.services.cache.cache import _memory

    _memory._data.clear()
    yield
    api_usage._MEM.clear()
    _memory._data.clear()


@pytest.fixture()
def client(db_session):
    from fastapi.testclient import TestClient

    from app.core.auth import CSRF_COOKIE, CSRF_HEADER
    from app.db.session import get_db
    from app.main import app

    class BrowserTestClient(TestClient):
        """模拟同源浏览器：unsafe 请求自动发送 Origin 与 double-submit 头。"""

        def request(self, method, url, **kwargs):
            headers = dict(kwargs.pop("headers", {}) or {})
            lowered = {key.lower() for key in headers}
            if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
                if "origin" not in lowered:
                    headers["Origin"] = "http://localhost:5173"
                if "sec-fetch-site" not in lowered:
                    headers["Sec-Fetch-Site"] = "same-origin"
                token = self.cookies.get(CSRF_COOKIE)
                if token and CSRF_HEADER.lower() not in lowered:
                    headers[CSRF_HEADER] = token
            return super().request(method, url, headers=headers, **kwargs)

        def server_request(self, method, url, **kwargs):
            """发送不伪造浏览器 Origin / Fetch Metadata / CSRF 头的请求。"""
            return super().request(method, url, **kwargs)

    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    with BrowserTestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
