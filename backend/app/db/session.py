"""数据库会话管理。"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

_is_sqlite = settings.database_url.startswith("sqlite")

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    **(
        {"connect_args": {"check_same_thread": False}}
        if _is_sqlite
        else {"pool_size": 5, "max_overflow": 5, "pool_recycle": 1800}
    ),
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
