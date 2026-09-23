"""API 用量管理：数据库原子计数，内存仅作故障降级与限流。"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models import ApiUsage

logger = get_logger(__name__)

DAILY_LIMITS: dict[str, int] = {
    "zhihu_search": 5000, "web_search": 5000, "zhihu_hot": 100,
    "zhihu_direct_answer": 100, "zhihu_knowledge": 100, "llm": 100000,
}
_MEM: dict[str, dict[str, int]] = {}
_MEM_LOCK = threading.Lock()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _increments(cache_hit: bool, failure: bool) -> dict[str, int]:
    return {
        "calls": 0 if cache_hit else 1,
        "cache_hits": 1 if cache_hit else 0,
        "cache_misses": 0 if cache_hit else 1,
        "failures": 1 if failure else 0,
    }


def _record_memory(provider: str, values: dict[str, int]) -> None:
    key = f"{provider}:{_today()}"
    with _MEM_LOCK:
        mem = _MEM.setdefault(key, {name: 0 for name in values})
        for name, value in values.items():
            mem[name] = int(mem.get(name, 0)) + value


def _atomic_update(db, provider: str, day: str, values: dict[str, int]) -> int:
    result = db.execute(
        update(ApiUsage)
        .where(ApiUsage.provider == provider, ApiUsage.usage_date == day)
        .values(
            calls=func.coalesce(ApiUsage.calls, 0) + values["calls"],
            cache_hits=func.coalesce(ApiUsage.cache_hits, 0) + values["cache_hits"],
            cache_misses=func.coalesce(ApiUsage.cache_misses, 0) + values["cache_misses"],
            failures=func.coalesce(ApiUsage.failures, 0) + values["failures"],
        )
    )
    return int(result.rowcount or 0)


def record(provider: str, endpoint: str = "", *, cache_hit: bool = False, failure: bool = False) -> None:
    values = _increments(cache_hit, failure)
    day = _today()
    db = SessionLocal()
    try:
        if _atomic_update(db, provider, day, values) == 0:
            db.add(ApiUsage(provider=provider, endpoint=endpoint, usage_date=day, **values))
            try:
                db.commit()
                return
            except IntegrityError:
                db.rollback()
                if _atomic_update(db, provider, day, values) == 0:
                    raise
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        _record_memory(provider, values)
        logger.warning("api_usage write failed: %s: %s", type(exc).__name__, str(exc)[:160])
    finally:
        db.close()


def snapshot() -> dict:
    out: dict[str, dict] = {}
    day = _today()
    rows: dict[str, ApiUsage] = {}
    db = SessionLocal()
    try:
        rows = {
            row.provider: row
            for row in db.execute(select(ApiUsage).where(ApiUsage.usage_date == day)).scalars().all()
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("api_usage read failed: %s", type(exc).__name__)
    finally:
        db.close()

    for provider, limit in DAILY_LIMITS.items():
        row = rows.get(provider)
        fallback = _MEM.get(f"{provider}:{day}", {})
        calls = int(row.calls or 0) if row is not None else int(fallback.get("calls", 0))
        hits = int(row.cache_hits or 0) if row is not None else int(fallback.get("cache_hits", 0))
        failures = int(row.failures or 0) if row is not None else int(fallback.get("failures", 0))
        out[provider] = {
            "calls": calls, "cache_hits": hits, "failures": failures,
            "daily_limit": limit, "near_limit": calls >= limit * 0.8,
        }
    return out


def near_limit(provider: str) -> bool:
    return bool(snapshot().get(provider, {}).get("near_limit"))


def rate_limit_hit(key: str, limit: int, window_seconds: int) -> bool:
    from app.services.cache import cache_incr

    return cache_incr(f"ratelimit:{key}", window_seconds) > limit
