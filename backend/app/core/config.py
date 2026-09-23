"""集中配置：全部来自环境变量，禁止硬编码 Secret。"""
from __future__ import annotations

import os
from functools import lru_cache
from urllib.parse import urlsplit

from dotenv import load_dotenv
from sqlalchemy import URL

from app.core.url_guard import normalize_provider_url

load_dotenv()


def _base_url(key: str) -> str:
    value = os.getenv(key, "").strip()
    return normalize_provider_url(value) if value else ""


def _bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _origin(value: str) -> str:
    """规范化精确 Origin；拒绝路径、通配符、凭据和不安全生产协议。"""
    value = value.strip()
    if not value or "*" in value:
        raise ValueError("CORS_ORIGINS 必须是非通配符的精确 Origin")
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"CORS Origin 不合法：{value}")
    if parsed.username is not None or parsed.password is not None or parsed.path not in ("", "/"):
        raise ValueError(f"CORS Origin 必须只包含 scheme、host 和 port：{value}")
    if parsed.query or parsed.fragment:
        raise ValueError(f"CORS Origin 不能包含查询或片段：{value}")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"CORS Origin 端口不合法：{value}") from exc
    host = parsed.hostname.lower().rstrip(".")
    host_part = f"[{host}]" if ":" in host else host
    default_port = 443 if parsed.scheme == "https" else 80
    return f"{parsed.scheme}://{host_part}" + (f":{port}" if port not in (None, default_port) else "")


class Settings:
    app_env: str = os.getenv("APP_ENV", "dev")
    app_host: str = os.getenv("APP_HOST", "127.0.0.1")
    app_port: int = int(os.getenv("APP_PORT", "8000"))
    cors_origins: list[str] = list(dict.fromkeys(
        _origin(o) for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()
    ))
    trusted_hosts: list[str] = list(dict.fromkeys(
        h.strip().lower() for h in os.getenv("TRUSTED_HOSTS", "").split(",") if h.strip()
    ))
    max_concurrent_cases: int = int(os.getenv("MAX_CONCURRENT_CASES", "3"))

    db_host: str = os.getenv("DB_HOST", "127.0.0.1")
    db_port: int = int(os.getenv("DB_PORT", "3306"))
    db_user: str = os.getenv("DB_USER", "zhicourt")
    db_password: str = os.getenv("DB_PASSWORD", "")
    db_name: str = os.getenv("DB_NAME", "zhicourt")

    redis_url: str = os.getenv("REDIS_URL", "")

    llm_base_url: str = _base_url("LLM_BASE_URL")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "")
    llm_timeout: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "90"))

    zhihu_api_base_url: str = _base_url("ZHIHU_API_BASE_URL")
    # 官方将该凭据称为 Access Secret。优先采用准确命名，同时兼容旧部署变量。
    zhihu_api_key: str = (
        os.getenv("ZHIHU_ACCESS_SECRET", "").strip()
        or os.getenv("ZHIHU_API_KEY", "").strip()
    )
    zhihu_search_path: str = os.getenv("ZHIHU_SEARCH_PATH", "/api/v1/content/zhihu_search")
    zhihu_hot_path: str = os.getenv("ZHIHU_HOT_PATH", "/api/v1/content/hot_list")
    zhihu_direct_answer_path: str = os.getenv("ZHIHU_DIRECT_ANSWER_PATH", "/v1/chat/completions")
    zhihu_knowledge_path: str = os.getenv("ZHIHU_KNOWLEDGE_PATH", "/api/v1/knowledge/search")

    # 知乎 OAuth 登录（黑客松赛事页面领取 App ID / App Key；App Key 仅服务端使用）
    zhihu_oauth_app_id: str = os.getenv("ZHIHU_OAUTH_APP_ID", "").strip()
    zhihu_oauth_app_key: str = os.getenv("ZHIHU_OAUTH_APP_KEY", "").strip()
    # 与赛事页面登记值完全一致（协议、域名、端口、路径、尾部斜杠）
    zhihu_oauth_redirect_uri: str = os.getenv("ZHIHU_OAUTH_REDIRECT_URI", "").strip()
    # 前端路由固定接收此路径，避免配置与构建产物漂移。
    zhihu_oauth_callback_path: str = "/login/zhihu"
    # OAuth 端点由赛事协议固定；不允许通过环境变量改到未知 origin 后外带 App Key。
    zhihu_oauth_openapi_base: str = "https://openapi.zhihu.com"

    web_search_base_url: str = _base_url("WEB_SEARCH_BASE_URL")
    web_search_api_key: str = os.getenv("WEB_SEARCH_API_KEY", "")
    web_search_path: str = os.getenv("WEB_SEARCH_PATH", "/api/v1/content/global_search")

    max_question_length: int = int(os.getenv("MAX_QUESTION_LENGTH", "500"))
    max_challenge_length: int = int(os.getenv("MAX_CHALLENGE_LENGTH", "1000"))

    # 允许整体覆盖连接串（测试用 SQLite）
    database_url_override: str = os.getenv("DATABASE_URL", "")

    # 每个案件的最大来源数 / 证据数（控制 token 与页面规模）
    max_sources_per_case: int = 12
    max_evidence_per_source: int = 4

    @property
    def database_url(self) -> str:
        if self.database_url_override:
            return self.database_url_override
        return URL.create(
            "mysql+pymysql",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
            query={"charset": "utf8mb4"},
        ).render_as_string(hide_password=False)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)

    @property
    def zhihu_real_enabled(self) -> bool:
        return bool(self.zhihu_api_base_url and self.zhihu_api_key)

    @property
    def zhihu_oauth_enabled(self) -> bool:
        if not (self.zhihu_oauth_app_id and self.zhihu_oauth_app_key and self.zhihu_oauth_redirect_uri):
            return False
        try:
            parsed = urlsplit(self.zhihu_oauth_redirect_uri)
            origin = _origin(f"{parsed.scheme}://{parsed.netloc}")
        except (TypeError, ValueError):
            return False
        return bool(
            parsed.path == self.zhihu_oauth_callback_path
            and not parsed.query
            and not parsed.fragment
            and parsed.username is None
            and parsed.password is None
            and origin in self.cors_origins
            and (not self.is_prod or parsed.scheme == "https")
        )

    @property
    def web_search_real_enabled(self) -> bool:
        return bool(self.web_search_base_url and self.web_search_api_key)

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"


@lru_cache
def get_settings() -> Settings:
    configured = Settings()
    if configured.is_prod:
        if not configured.cors_origins or any(not origin.startswith("https://") for origin in configured.cors_origins):
            raise ValueError("生产环境 CORS_ORIGINS 必须是非空的精确 HTTPS Origin allowlist")
        if not configured.trusted_hosts:
            raise ValueError("生产环境 TRUSTED_HOSTS 不能为空")
        if not configured.database_url_override and not configured.db_password:
            raise ValueError("生产环境必须配置 DB_PASSWORD")
    return configured


settings = get_settings()
