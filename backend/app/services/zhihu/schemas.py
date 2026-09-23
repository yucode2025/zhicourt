"""Provider 统一归一化 Schema。

真实 API 返回的原始字段必须先在这里转换为内部结构，
上层（Agent / Workflow）只消费归一化结构。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.url_guard import public_source_url


class NormalizedSearchResult(BaseModel):
    origin: Literal["zhihu", "web"]
    kind: str = ""           # answer | article | question | page | report ...
    title: str
    url: str = ""
    author: str = ""
    summary: str = ""
    published_at: str = ""   # 原始字符串，尽力解析
    vote_count: int = 0      # 知乎赞同 / web 中为 0 或近似信号
    comment_count: int = 0
    # 官方 global_search 的 AuthorityLevel；不持久化到 Source，供当前轮排序使用。
    official_authority_level: int | None = None
    # 演示 Provider 显式传递分类提示；真实来源保持 None，由 Evidence Agent 判断。
    stance_hint: Literal["pro", "con", "neutral"] | None = None
    evidence_type_hint: Literal[
        "fact", "opinion", "data", "case", "prediction", "assumption", "limitation"
    ] | None = None
    is_demo_source: bool = False

    @field_validator("url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        return public_source_url(value)


class NormalizedHotItem(BaseModel):
    title: str
    url: str = ""
    heat: int = 0
    excerpt: str = ""

    @field_validator("url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        return public_source_url(value)


class NormalizedDirectAnswer(BaseModel):
    answer: str = ""
    key_concepts: list[str] = Field(default_factory=list)
    disputes: list[str] = Field(default_factory=list)
    sub_questions: list[str] = Field(default_factory=list)


class ZhihuSearchResponse(BaseModel):
    items: list[NormalizedSearchResult] = Field(default_factory=list)
    is_demo: bool = False
