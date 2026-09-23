"""Provider 运行时配置：管理后台可填，保存即生效（无需重启）。

优先级：数据库配置（管理员填写） > .env 环境变量 > 官方默认 > Mock。
官方默认（zhihu skill 包 0.5.3 核验）：Base URL https://developer.zhihu.com，
路径 /api/v1/content/zhihu_search 等——因此管理员只需填写 Access Secret 即可切到真实模式。
安全约定：
- API Key 存 DB（system_settings），任何读取接口只返回脱敏形式（保留末 4 位）
- 前端提交时若值仍为掩码形态则视为"未修改"，保留原值
- 保存动作写审计日志
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.crypto import decrypt, encrypt
from app.core.url_guard import (
    UnsafeURLError,
    ensure_provider_url_basic,
    normalize_provider_url,
    same_provider_origin,
)
from app.models import SystemSetting
from app.services.zhihu.client import (
    OFFICIAL_BASE_URL,
    PATH_GLOBAL_SEARCH,
    PATH_HOT_LIST,
    PATH_KNOWLEDGE_SEARCH,
    PATH_ZHIDA,
    PATH_ZHIHU_SEARCH,
    validate_api_path,
)

CONFIG_KEY = "provider_config"

FIELDS = [
    # key, 是否 secret, 说明
    ("zhihu_api_key", True, "知乎开放平台 Access Secret（developer.zhihu.com 个人中心获取）"),
    ("zhihu_base_url", False, "知乎 API Base URL（留空 = 官方默认 https://developer.zhihu.com）"),
    ("zhihu_search_path", False, "知乎搜索路径（留空 = 官方默认）"),
    ("zhihu_hot_path", False, "知乎热榜路径（留空 = 官方默认）"),
    ("zhihu_direct_answer_path", False, "直答 Agent 路径（留空 = 官方默认）"),
    ("zhihu_knowledge_path", False, "知识库 RAG 路径（留空 = 官方默认）"),
    ("web_api_key", True, "全网搜索 Access Secret（与知乎搜索共用同一 Secret 时填同一个）"),
    ("web_base_url", False, "全网搜索 Base URL（留空 = 官方默认）"),
    ("web_search_path", False, "全网搜索路径（留空 = 官方默认）"),
    ("zhihu_oauth_app_id", False, "知乎黑客松赛事页面分配的 App ID"),
    ("zhihu_oauth_app_key", True, "知乎 OAuth App Key（仅服务端加密保存）"),
    ("zhihu_oauth_redirect_uri", False, "赛事页面登记的 HTTPS 回调地址，路径必须为 /login/zhihu"),
    ("llm_base_url", False, "LLM Base URL（OpenAI 兼容，如 https://api.xxx.com/v1）"),
    ("llm_api_key", True, "LLM API Key（加密保存）"),
    ("llm_model", False, "LLM 模型名（如 gpt-4o-mini / deepseek-chat）"),
    ("llm_fallback_1_base_url", False, "备用 LLM 1 Base URL（主接口失败时自动切换）"),
    ("llm_fallback_1_api_key", True, "备用 LLM 1 API Key（加密保存）"),
    ("llm_fallback_1_model", False, "备用 LLM 1 模型名"),
    ("llm_fallback_2_base_url", False, "备用 LLM 2 Base URL（前两组失败时自动切换）"),
    ("llm_fallback_2_api_key", True, "备用 LLM 2 API Key（加密保存）"),
    ("llm_fallback_2_model", False, "备用 LLM 2 模型名"),
]

LLM_PREFIXES = ("llm", "llm_fallback_1", "llm_fallback_2")

SECRET_FIELDS = {f for f, secret, _ in FIELDS if secret}
BASE_URL_FIELDS = {f for f, _, _ in FIELDS if f.endswith("_base_url")}
CREDENTIAL_PAIRS = {
    "zhihu_base_url": "zhihu_api_key",
    "web_base_url": "web_api_key",
    "llm_base_url": "llm_api_key",
    "llm_fallback_1_base_url": "llm_fallback_1_api_key",
    "llm_fallback_2_base_url": "llm_fallback_2_api_key",
}
MASK_PREFIX = "••••"
# 加密保存的显式空值，阻止“清空”后静默回退到环境变量中的旧密钥。
CLEARED_SECRET = "__ZHICOURT_SECRET_EXPLICITLY_CLEARED__"

# 官方默认路径（Base URL / 路径留空时使用，管理员只需填 Access Secret）
OFFICIAL_DEFAULTS = {
    "zhihu_base_url": OFFICIAL_BASE_URL,
    "zhihu_search_path": PATH_ZHIHU_SEARCH,
    "zhihu_hot_path": PATH_HOT_LIST,
    "zhihu_direct_answer_path": PATH_ZHIDA,
    "zhihu_knowledge_path": PATH_KNOWLEDGE_SEARCH,
    "web_base_url": OFFICIAL_BASE_URL,
    "web_search_path": PATH_GLOBAL_SEARCH,
}


def _load_stored(db: Session) -> dict[str, str]:
    """读取存储值：secret 字段解密为明文（调用方谨慎使用，禁止原样返回给前端）。"""
    row = db.get(SystemSetting, CONFIG_KEY)
    if row is None or not isinstance(row.value, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in row.value.items():
        v = str(v) if v else ""
        if not v:
            continue
        if k in SECRET_FIELDS:
            decrypted = decrypt(v)
            out[k] = "" if decrypted == CLEARED_SECRET else decrypted
        else:
            out[k] = v
    return out


def _env_map() -> dict[str, str]:
    return {
        "zhihu_base_url": settings.zhihu_api_base_url,
        "zhihu_api_key": settings.zhihu_api_key,
        "zhihu_search_path": settings.zhihu_search_path,
        "zhihu_hot_path": settings.zhihu_hot_path,
        "zhihu_direct_answer_path": settings.zhihu_direct_answer_path,
        "zhihu_knowledge_path": settings.zhihu_knowledge_path,
        "web_base_url": settings.web_search_base_url,
        "web_api_key": settings.web_search_api_key,
        "web_search_path": settings.web_search_path,
        "zhihu_oauth_app_id": settings.zhihu_oauth_app_id,
        "zhihu_oauth_app_key": settings.zhihu_oauth_app_key,
        "zhihu_oauth_redirect_uri": settings.zhihu_oauth_redirect_uri,
        "llm_base_url": settings.llm_base_url,
        "llm_api_key": settings.llm_api_key,
        "llm_model": settings.llm_model,
        "llm_fallback_1_base_url": "",
        "llm_fallback_1_api_key": "",
        "llm_fallback_1_model": "",
        "llm_fallback_2_base_url": "",
        "llm_fallback_2_api_key": "",
        "llm_fallback_2_model": "",
    }


def _effective_from_stored(stored: dict[str, str]) -> dict[str, str]:
    env_map = _env_map()
    out: dict[str, str] = {}
    for key, _, _ in FIELDS:
        # DB 中显式空 secret 也具有优先级，用于阻止回退到 env 旧密钥。
        val = stored[key] if key in stored else (env_map.get(key, "") or OFFICIAL_DEFAULTS.get(key, ""))
        value = (val or "").strip()
        out[key] = normalize_provider_url(value) if key in BASE_URL_FIELDS and value else value

    # 官方接口允许同源复用同一个 Secret；绝不跨 origin，也不覆盖显式清空。
    if (
        not out["web_api_key"]
        and "web_api_key" not in stored
        and out["zhihu_api_key"]
        and same_provider_origin(out["web_base_url"], out["zhihu_base_url"])
    ):
        out["web_api_key"] = out["zhihu_api_key"]
    return out


def get_effective(db: Session) -> dict[str, str]:
    """数据库配置优先，其次 env，最后官方默认。"""
    return _effective_from_stored(_load_stored(db))


def llm_config(db: Session) -> dict[str, str]:
    """LLM 运行时配置（DB > env），供 Gateway 热更新。"""
    eff = get_effective(db)
    return {
        "base_url": eff["llm_base_url"],
        "api_key": eff["llm_api_key"],
        "model": eff["llm_model"],
    }


def llm_configs(db: Session) -> list[dict[str, str]]:
    """按主接口、备用 1、备用 2 的顺序返回完整 LLM 上游。"""
    eff = get_effective(db)
    out: list[dict[str, str]] = []
    for prefix in LLM_PREFIXES:
        item = {
            "base_url": eff[f"{prefix}_base_url"],
            "api_key": eff[f"{prefix}_api_key"],
            "model": eff[f"{prefix}_model"],
        }
        if all(item.values()):
            out.append(item)
    return out


def sync_llm_gateway(db: Session):
    """Synchronize this worker's in-memory gateway from the DB-backed endpoint pool."""
    from app.services.llm.gateway import gateway

    gateway.configure_endpoints(llm_configs(db), fallback_to_env=False)
    return gateway


def zhihu_real(db: Session) -> bool:
    c = get_effective(db)
    return bool(c["zhihu_base_url"] and c["zhihu_api_key"])


def web_real(db: Session) -> bool:
    c = get_effective(db)
    return bool(c["web_base_url"] and c["web_api_key"])


def oauth_config(db: Session) -> dict[str, str]:
    eff = get_effective(db)
    return {
        "app_id": eff["zhihu_oauth_app_id"],
        "app_key": eff["zhihu_oauth_app_key"],
        "redirect_uri": eff["zhihu_oauth_redirect_uri"],
    }


def oauth_enabled(db: Session) -> bool:
    from app.services.zhihu_oauth import OAuthRuntimeConfig, oauth_config_enabled

    return oauth_config_enabled(OAuthRuntimeConfig(**oauth_config(db)))


def masked_view(db: Session) -> dict[str, Any]:
    """给前端的脱敏视图：secret 只显示掩码 + 是否来自 DB。"""
    eff = get_effective(db)
    stored = _load_stored(db)
    items = []
    for key, is_secret, desc in FIELDS:
        val = eff[key]
        from_db = key in stored
        if is_secret:
            shown = (MASK_PREFIX + val[-4:]) if val else ""
        else:
            shown = val
        items.append(
            {
                "key": key,
                "description": desc,
                "is_secret": is_secret,
                "value": shown,
                "from_db": from_db,
                "set": bool(val),
                "group": (
                    "llm" if key.startswith("llm_")
                    else "oauth" if key.startswith("zhihu_oauth_")
                    else "content"
                ),
            }
        )
    from app.services.system_settings import get_flag

    gateway = sync_llm_gateway(db)
    mock_enabled = get_flag(db, "enable_mock_provider")
    return {
        "items": items,
        "mode": {
            "zhihu": "REAL" if zhihu_real(db) else "MOCK" if mock_enabled else "DISABLED",
            "web": "REAL" if web_real(db) else "MOCK" if mock_enabled else "DISABLED",
            "llm": "REAL" if gateway.enabled else "FALLBACK",
            "oauth": "REAL" if oauth_enabled(db) else "DISABLED",
        },
    }


def save_fields(db: Session, payload: dict[str, str]) -> dict[str, str]:
    """原子保存管理员配置，并阻止密钥随 Base URL 跨 origin 外带。

    secret 掩码表示不修改；空串表示显式清空且不再回退 env。Base URL 的
    origin 发生变化时，对应 secret 必须在同一次请求中提供新值或显式清空。
    """
    known_fields = {f for f, _, _ in FIELDS}
    submitted: dict[str, str] = {}
    for key, raw_value in payload.items():
        if key not in known_fields:
            continue
        value = (raw_value or "").strip()
        if key in BASE_URL_FIELDS and value:
            try:
                value = ensure_provider_url_basic(value)
            except UnsafeURLError as exc:
                raise ValueError(f"{key}: {exc}") from exc
        if key.endswith("_path") and value:
            try:
                validate_api_path(value)
            except ValueError as exc:
                raise ValueError(f"{key}: {exc}") from exc
        submitted[key] = value

    stored = _load_stored(db)
    before = _effective_from_stored(stored)
    candidate = dict(stored)
    for key, value in submitted.items():
        if key in SECRET_FIELDS:
            if value.startswith(MASK_PREFIX):
                continue
            candidate[key] = value  # 空串是有意义的显式清空
        elif value:
            candidate[key] = value
        else:
            candidate.pop(key, None)
    after = _effective_from_stored(candidate)

    if "zhihu_oauth_redirect_uri" in submitted and after["zhihu_oauth_redirect_uri"]:
        from app.services.zhihu_oauth import redirect_uri_valid

        if not redirect_uri_valid(after["zhihu_oauth_redirect_uri"]):
            raise ValueError(
                "zhihu_oauth_redirect_uri: 必须是本站允许 Origin 下的 HTTPS /login/zhihu 地址，且不能含查询参数或片段"
            )

    for base_field, secret_field in CREDENTIAL_PAIRS.items():
        if base_field not in submitted:
            continue
        try:
            if before[base_field] and after[base_field]:
                origin_changed = not same_provider_origin(before[base_field], after[base_field])
            else:
                origin_changed = bool(before[base_field]) != bool(after[base_field])
        except UnsafeURLError as exc:
            raise ValueError(f"{base_field}: {exc}") from exc
        supplied_secret = submitted.get(secret_field)
        if origin_changed and (supplied_secret is None or supplied_secret.startswith(MASK_PREFIX)):
            raise ValueError(
                f"{base_field}: Base URL 跨 origin 变更时必须同时提供新的 {secret_field} 或明确清空"
            )

    changes: dict[str, str] = {}
    for key, value in submitted.items():
        if key in SECRET_FIELDS and value.startswith(MASK_PREFIX):
            continue
        if key in SECRET_FIELDS:
            if candidate.get(key) != stored.get(key) or key not in stored:
                changes[key] = "cleared" if value == "" else "updated"
        elif value:
            if stored.get(key) != value:
                changes[key] = "updated"
        elif key in stored:
            changes[key] = "cleared"

    row = db.get(SystemSetting, CONFIG_KEY)
    if candidate:
        persisted = {
            key: encrypt(CLEARED_SECRET if value == "" else value) if key in SECRET_FIELDS else value
            for key, value in candidate.items()
        }
        if row is None:
            row = SystemSetting(key=CONFIG_KEY, value=persisted)
            db.add(row)
        else:
            row.value = persisted
    elif row is not None:
        db.delete(row)
    db.commit()
    # Centralize hot-update for callers beyond the admin route as well.
    if any(key.startswith("llm_") for key in changes):
        sync_llm_gateway(db)
    return changes
