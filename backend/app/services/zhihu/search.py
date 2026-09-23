"""Zhihu Search Adapter：业务代码唯一入口。

用法：
    provider = get_zhihu_provider(db)     # 读取管理后台运行时配置（DB）优先，env 兜底
    results = await provider.search(query)
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.provider_config import get_effective, web_real, zhihu_real
from app.services.system_settings import get_flag
from app.services.zhihu.client import RealWebSearchProvider, RealZhihuProvider, ZhihuProviderError
from app.services.zhihu.mock_data import MockWebSearchProvider, MockZhihuProvider


def get_zhihu_provider(db: Session | None = None):
    """db 提供时读取管理后台运行时配置；否则仅按 env 判断。"""
    if db is not None and zhihu_real(db):
        eff = get_effective(db)
        return RealZhihuProvider(api_key=eff["zhihu_api_key"], base_url=eff["zhihu_base_url"], paths=eff)
    if db is None and settings.zhihu_real_enabled:
        return RealZhihuProvider(api_key=settings.zhihu_api_key, base_url=settings.zhihu_api_base_url)
    if db is not None and not get_flag(db, "enable_mock_provider"):
        raise ZhihuProviderError("知乎搜索不可用：未配置真实 Provider，且 Mock 已关闭")
    return MockZhihuProvider()


def get_web_search_provider(db: Session | None = None):
    if db is not None and web_real(db):
        eff = get_effective(db)
        return RealWebSearchProvider(api_key=eff["web_api_key"], base_url=eff["web_base_url"], paths=eff)
    if db is None and settings.web_search_real_enabled:
        return RealWebSearchProvider(api_key=settings.web_search_api_key, base_url=settings.web_search_base_url)
    if db is not None and not get_flag(db, "enable_mock_provider"):
        raise ZhihuProviderError("全网搜索不可用：未配置真实 Provider，且 Mock 已关闭")
    return MockWebSearchProvider()
