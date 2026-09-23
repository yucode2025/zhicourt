"""FastAPI 应用入口。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.routes_cases import router as cases_router, share_router
from app.api.routes_auth import router as auth_router
from app.api.routes_admin import router as admin_router
from app.core.auth import (
    ANON_COOKIE,
    CSRF_COOKIE,
    CSRF_HEADER,
    SESSION_COOKIE,
    csrf_tokens_match,
    new_csrf_token,
)
from app.core.config import settings
from app.core.logging import setup_logging
from app.db.session import get_db
from app.schemas.case import ErrorBody, ErrorResponse
from app.services.system_settings import get_flag
from app.api.access_policy import resolve_usage_access
from app.services.llm.gateway import (
    LLMError, llm_error_http_status, llm_error_is_retryable, llm_error_public_message,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    # 启动时从 DB 加载 LLM 运行时配置（管理后台保存后热更新，此处兜底冷启动）
    from app.db.session import SessionLocal
    from app.services import provider_config
    from app.services.llm import gateway
    memory_database = settings.database_url in ("sqlite+pysqlite://", "sqlite+pysqlite:///:memory:")
    if not memory_database:
        try:
            db = SessionLocal()
            try:
                gateway.configure_endpoints(
                    provider_config.llm_configs(db), fallback_to_env=False
                )
            finally:
                db.close()
        except Exception:  # noqa: BLE001 — DB 不可用时不阻断启动
            pass
    workflow_manager = None
    try:
        from app.workflows import manager as workflow_manager

        if not memory_database:
            workflow_manager.start_recovery_sweeper()
            workflow_manager.recover_cases()
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).exception("failed to recover interrupted cases")
    try:
        yield
    finally:
        if workflow_manager is not None:
            workflow_manager.stop_recovery_sweeper()
        # 释放连接池，避免开发热重载、测试进程和优雅停机留下数据库句柄。
        from app.db.session import engine

        engine.dispose()


app = FastAPI(
    title="ZhiCourt · 知识法庭 API",
    description="让观点接受证据审理。Source → Evidence → Claim → Argument → Verdict 完整追溯。",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_HEALTH_PATHS = {"/api/health", "/api/health/live", "/api/health/ready"}
_CRITICAL_AGENT_RUN_COLUMNS = frozenset({
    "run_generation",
    "stage",
    "attempt",
    "reused_from_generation",
    "error_code",
    "error_kind",
})


@lru_cache(maxsize=1)
def _release_migration_heads() -> frozenset[str]:
    """Return the Alembic heads shipped with this exact application release."""
    from alembic.script import ScriptDirectory

    backend_dir = Path(__file__).resolve().parent.parent
    return frozenset(ScriptDirectory(str(backend_dir / "migrations")).get_heads())


def _database_schema_matches_release(db) -> bool:
    """Reject traffic until the DB revision and critical execution schema match."""
    from sqlalchemy import inspect, text

    current_heads = frozenset(
        str(value)
        for value in db.execute(text("SELECT version_num FROM alembic_version")).scalars()
    )
    if current_heads != _release_migration_heads():
        return False

    inspector = inspect(db.connection())
    if "agent_runs" not in inspector.get_table_names():
        return False
    columns = {column["name"] for column in inspector.get_columns("agent_runs")}
    return _CRITICAL_AGENT_RUN_COLUMNS <= columns


@app.middleware("http")
async def request_security(request: Request, call_next):
    """强制 HTTPS、Fetch Metadata、精确 Origin 与 cookie 身份的 double-submit CSRF。"""
    loopback_health = (
        request.url.path in _HEALTH_PATHS
        and request.url.hostname in {"localhost", "127.0.0.1", "::1"}
    )
    if settings.is_prod and request.url.scheme != "https" and not loopback_health:
        return JSONResponse(status_code=400, content={"detail": "生产环境仅允许 HTTPS"})

    if request.method in _UNSAFE_METHODS:
        # Origin / Fetch Metadata 是浏览器信号：信号存在时必须可信，但不能要求
        # 不携带这些浏览器头的 CLI、webhook 或服务间调用伪造一个 Origin。
        origin = request.headers.get("origin")
        if origin is not None and origin not in settings.cors_origins:
            return JSONResponse(status_code=403, content={"detail": "请求 Origin 不可信"})
        fetch_site = request.headers.get("sec-fetch-site", "").lower()
        if fetch_site in {"cross-site", "none"}:
            return JSONResponse(status_code=403, content={"detail": "跨站请求已拒绝"})

        # 身份 cookie 是浏览器可自动附带的 ambient authority；无论 Origin 等头是否
        # 存在，只要请求携带已有身份，就必须通过 double-submit CSRF 校验。
        has_identity = bool(
            request.cookies.get(SESSION_COOKIE) or request.cookies.get(ANON_COOKIE)
        )
        if has_identity and not csrf_tokens_match(
            request.cookies.get(CSRF_COOKIE), request.headers.get(CSRF_HEADER)
        ):
            return JSONResponse(status_code=403, content={"detail": "CSRF 校验失败"})

    response = await call_next(request)
    # 兼容升级前已存在的身份 cookie：通过安全请求补发可读 CSRF cookie。
    if (
        request.method in {"GET", "HEAD"}
        and (request.cookies.get(SESSION_COOKIE) or request.cookies.get(ANON_COOKIE))
        and not request.cookies.get(CSRF_COOKIE)
    ):
        response.set_cookie(
            CSRF_COOKIE, new_csrf_token(), max_age=3600 * 24 * 90,
            httponly=False, samesite="lax", secure=settings.is_prod,
        )
    return response


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api/auth/zhihu"):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
    elif "Referrer-Policy" not in response.headers:
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if settings.is_prod:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# 注册顺序使 TrustedHost 最外层、CORS 位于 CSRF 外层。
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    # 与实际 API 方法保持一致：PATCH /api/auth/me、DELETE /api/cases/{id}/favorite
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)
_trusted_hosts = list(dict.fromkeys([
    *settings.trusted_hosts,
    "localhost",
    "127.0.0.1",
    "[::1]",
    *([] if settings.is_prod else ["testserver"]),
]))
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_trusted_hosts)


@app.exception_handler(LLMError)
async def llm_exception_handler(request: Request, exc: LLMError):
    import logging

    status = llm_error_http_status(exc)
    logging.getLogger(__name__).warning(
        "LLM request failed: %s %s code=%s", request.method, request.url.path, exc.code,
    )
    headers = {"Retry-After": "30"} if llm_error_is_retryable(exc) else None
    return JSONResponse(
        status_code=status, headers=headers,
        content=ErrorResponse(
            error=ErrorBody(code=exc.code, message=llm_error_public_message(exc), detail=None)
        ).model_dump(),
    )



@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    import logging

    logging.getLogger(__name__).exception("unhandled request error: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(error=ErrorBody(code="internal_error", message="服务器内部错误", detail=None)).model_dump(),
    )


app.include_router(cases_router)
app.include_router(share_router)
app.include_router(auth_router)
app.include_router(admin_router)


@app.get("/api/capabilities", tags=["system"])
async def capabilities(request: Request, db = Depends(get_db)):
    from app.services import provider_config

    access = resolve_usage_access(request, db)
    required_action = "none" if access.allowed else "zhihu" if access.policy == "zhihu" else "login"
    response = JSONResponse(content={
        "usage_access_policy": access.policy,
        "can_use": access.allowed,
        "required_action": required_action,
        "zhihu_oauth": {
            "enabled": provider_config.oauth_enabled(db),
            "callback_path": settings.zhihu_oauth_callback_path,
        },
    })
    response.headers["Cache-Control"] = "private, no-store"
    return response


@app.get("/api/health/live", tags=["system"])
async def liveness():
    return {"status": "ok"}


@app.get("/api/health", tags=["system"])
@app.get("/api/health/ready", tags=["system"])
async def readiness(db = Depends(get_db)):
    from sqlalchemy import text

    from app.services import provider_config
    from app.services.llm import gateway

    try:
        db.execute(text("SELECT 1"))
        if not _database_schema_matches_release(db):
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "database": "schema_mismatch", "env": settings.app_env},
            )
        mock_enabled = get_flag(db, "enable_mock_provider")
        zhihu_mode = "real" if provider_config.zhihu_real(db) else "demo" if mock_enabled else "disabled"
        web_mode = (
            "disabled" if not get_flag(db, "enable_web_search")
            else "real" if provider_config.web_real(db)
            else "demo" if mock_enabled
            else "disabled"
        )
    except Exception:  # noqa: BLE001
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "database": "unavailable", "env": settings.app_env},
        )
    return {"status": "ok", "database": "ok", "env": settings.app_env,
            "llm": "llm" if gateway.enabled else "heuristic",
            "zhihu": zhihu_mode, "web_search": web_mode}
