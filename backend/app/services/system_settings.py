"""系统设置与 Feature Flags（存数据库，非 Secret）。

Provider Secret 可由管理后台加密保存或由 .env 提供；本模块只管理非 Secret Flag，
并为环境变量型 Secret 提供 Configured / Missing 状态。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import SystemSetting

logger = logging.getLogger(__name__)

# 默认值：key -> (默认值, 说明, 是否 feature flag)
DEFAULTS: dict[str, tuple[object, str, bool]] = {
    # Feature Flags
    "allow_guest_cases": (True, "允许游客（匿名会话）创建案件", True),
    "enable_hot_cases": (True, "启用知乎热榜", True),
    "enable_web_search": (True, "启用全网搜索交叉验证", True),
    "enable_direct_answer": (False, "启用直答 Agent（仅真实知乎 Provider 可用）", True),
    "enable_zhihu_knowledge": (False, "知乎知识尚缺 KnowledgeBaseIDs allowlist，不参与案件证据", True),
    "enable_mock_provider": (True, "未配置官方 Key 时允许 Mock Provider（关闭则搜索不可用）", True),
    # 运行配置
    "usage_access_policy": ("guest", "业务功能访问策略：guest / authenticated / zhihu", False),
    "max_sources_per_case": (12, "每案件最大来源数", False),
    "evidence_top_k_per_source": (4, "每来源最大证据条数", False),
    "case_input_max_length": (500, "问题最大长度", False),
    "challenge_input_max_length": (1000, "质询最大长度", False),
    "search_cache_ttl_hours": (6, "搜索缓存 TTL（小时）", False),
}

_FLAG_KEYS = [k for k, (_, _, flag) in DEFAULTS.items() if flag]
ENUM_VALUES: dict[str, frozenset[str]] = {
    "usage_access_policy": frozenset({"guest", "authenticated", "zhihu"}),
}
INT_RANGES: dict[str, tuple[int, int]] = {
    "max_sources_per_case": (1, 50),
    "evidence_top_k_per_source": (1, 10),
    "case_input_max_length": (20, 5000),
    "challenge_input_max_length": (20, 5000),
    "search_cache_ttl_hours": (1, 168),
}


def get_setting(db: Session, key: str):
    if key not in DEFAULTS:
        raise KeyError(key)
    default = DEFAULTS[key][0]
    row = db.get(SystemSetting, key)
    if row is None or row.value is None:
        return default
    value = row.value
    if type(value) is not type(default):
        logger.warning("invalid persisted system setting type for %s; applying safe fallback", key)
        return "authenticated" if key == "usage_access_policy" else default
    if key in ENUM_VALUES and value not in ENUM_VALUES[key]:
        # 访问控制设置损坏时不能回退到更宽松的默认值。
        logger.warning("invalid persisted system setting enum for %s; applying safe fallback", key)
        return "authenticated" if key == "usage_access_policy" else default
    if key in INT_RANGES and not INT_RANGES[key][0] <= value <= INT_RANGES[key][1]:
        return default
    return value


def get_flag(db: Session, key: str) -> bool:
    return bool(get_setting(db, key))


def get_all(db: Session) -> list[dict]:
    out = []
    for key, (default, desc, is_flag) in DEFAULTS.items():
        row = db.get(SystemSetting, key)
        out.append(
            {
                "key": key,
                "value": get_setting(db, key),
                "default": default,
                "description": desc,
                "is_flag": is_flag,
                "updated_at": row.updated_at.isoformat() + "Z" if row is not None and row.updated_at else None,
                "updated_by": row.updated_by if row is not None else "",
            }
        )
    return out


def set_setting(db: Session, key: str, value, updated_by: str = "") -> None:
    if key not in DEFAULTS:
        raise KeyError(key)
    expected_type = type(DEFAULTS[key][0])
    if expected_type is bool:
        if not isinstance(value, bool):
            raise ValueError("设置值必须是布尔值")
    elif expected_type is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("设置值必须是整数")
        if key in INT_RANGES:
            low, high = INT_RANGES[key]
            if not low <= value <= high:
                raise ValueError(f"设置值必须在 {low} 到 {high} 之间")
    elif type(value) is not expected_type:
        raise ValueError("设置值类型不正确")
    if key in ENUM_VALUES and value not in ENUM_VALUES[key]:
        allowed = " / ".join(sorted(ENUM_VALUES[key]))
        raise ValueError(f"设置值必须是 {allowed} 之一")
    row = db.get(SystemSetting, key)
    if row is None:
        row = SystemSetting(key=key, value=value, updated_by=updated_by)
        db.add(row)
    else:
        row.value = value
        row.updated_by = updated_by
    db.commit()


def secret_status(db: Session) -> list[dict]:
    """环境变量和数据库 Provider Secret 的统一脱敏状态。"""
    from app.core.config import settings
    from app.services.provider_config import get_effective

    effective = get_effective(db)

    def st(v: str) -> str:
        return "configured" if v else "missing"

    return [
        {"name": "LLM_BASE_URL", "status": st(effective["llm_base_url"])},
        {"name": "LLM_API_KEY", "status": st(effective["llm_api_key"])},
        {"name": "LLM_MODEL", "status": st(effective["llm_model"])},
        {"name": "ZHIHU_API_BASE_URL", "status": st(effective["zhihu_base_url"])},
        {"name": "ZHIHU_ACCESS_SECRET", "status": st(effective["zhihu_api_key"])},
        {"name": "WEB_SEARCH_BASE_URL", "status": st(effective["web_base_url"])},
        {"name": "WEB_SEARCH_API_KEY", "status": st(effective["web_api_key"])},
        {"name": "ZHIHU_OAUTH_APP_ID", "status": st(effective["zhihu_oauth_app_id"])},
        {"name": "ZHIHU_OAUTH_APP_KEY", "status": st(effective["zhihu_oauth_app_key"])},
        {"name": "ZHIHU_OAUTH_REDIRECT_URI", "status": st(effective["zhihu_oauth_redirect_uri"])},
        {"name": "DB_PASSWORD", "status": st(settings.db_password)},
        {"name": "REDIS_URL", "status": st(settings.redis_url)},
    ]
