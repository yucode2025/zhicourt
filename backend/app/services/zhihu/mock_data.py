"""Mock Provider：仅用于未配置官方 API Key 的演示模式。

规则：
- 一切 Mock 搜索结果都带 is_demo_source=True，供后续流程显式标记。
- 禁止伪造真实作者、URL、热度、日期、数据、案例或外部观察。
- 演示语料只展示论证/核验结构，不代表任何现实事实或真实用户发言。
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlencode

from app.services.zhihu.schemas import (
    NormalizedDirectAnswer,
    NormalizedHotItem,
    NormalizedSearchResult,
)


def _topic(query: str) -> str:
    """提取命题主干：去掉疑问词/标点，保留可读的主题文本。"""
    text = query.strip()
    text = re.sub(r"^(请问|你觉得|你认为|大家觉得|为什么|如何看|怎么看|谈谈)(?=[^，。？?！!])", "", text)
    text = re.sub(r"[？?！!。,，、\s]+$", "", text)
    text = text.rstrip("吗呢吧啊")
    return text[:80] or "该命题"


def _query_hash(query: str) -> str:
    """为查询生成跨进程稳定、不可反推出原文的短标识。"""
    normalized = " ".join(query.strip().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _demo_url(
    origin: str,
    index: int,
    query: str,
    *,
    stance_hint: str | None = None,
    evidence_type_hint: str | None = None,
) -> str:
    params = {"query_hash": _query_hash(query)}
    # Source 模型不持久化演示分类提示；将它们同时放入演示 URL，供 Evidence Agent 传递使用。
    if stance_hint:
        params["stance_hint"] = stance_hint
    if evidence_type_hint:
        params["evidence_type_hint"] = evidence_type_hint
    return f"demo://{origin}/{index}?{urlencode(params)}"


# 以下模板只描述如何核验命题，不声称任何领域事实。类型提示也仅表示演示论证的结构类型。
_PRO_TEMPLATES = [
    (
        "支持方向的演示观点：本条只把「{t}」作为待论证命题；必须由可追溯来源证明命题所述结果，才能形成支持。",
        "opinion",
    ),
    (
        "支持方向的演示前提：若可靠来源能排除主要替代解释，并显示「{t}」在明确范围内成立，则支持力度会上升。",
        "assumption",
    ),
    (
        "支持方向的演示核验项：应为「{t}」预先定义可观测指标、比较基准和适用时间，再检查真实材料。",
        "limitation",
    ),
    (
        "支持方向的演示观点：对「{t}」的支持不能来自本演示文本，而应来自相互独立且结论一致的真实来源。",
        "opinion",
    ),
]

_CON_TEMPLATES = [
    (
        "反对方向的演示观点：本条只展示反方立场；若可靠来源给出与「{t}」不一致的结果，命题的适用范围需要收窄。",
        "opinion",
    ),
    (
        "反对方向的演示前提：若「{t}」依赖的关键条件无法成立，或存在更强替代解释，则不能据此接受命题。",
        "assumption",
    ),
    (
        "反对方向的演示核验项：应主动检索「{t}」的反例，并确认样本、口径和时间范围是否可比。",
        "limitation",
    ),
    (
        "反对方向的演示观点：反驳「{t}」同样需要可追溯的真实材料，本演示文本本身不构成反证。",
        "opinion",
    ),
]

_NEUTRAL_TEMPLATES = [
    (
        "中立演示核验项：判断「{t}」前需要明确核心术语、适用对象、时间范围与判断阈值。",
        "limitation",
    ),
    (
        "中立演示核验项：本演示未提供真实来源，不能据此确认或否定「{t}」，需要补充可追溯材料。",
        "limitation",
    ),
]


def _demo_results(query: str, origin: str, count: int) -> list[NormalizedSearchResult]:
    topic = _topic(query)
    results: list[NormalizedSearchResult] = []
    for i in range(count):
        idx = i % 4
        if i % 5 == 4:
            tpl, evidence_type_hint = _NEUTRAL_TEMPLATES[idx % len(_NEUTRAL_TEMPLATES)]
            stance_hint = "neutral"
            stance_label = "中立核验"
        elif i % 2 == 0:
            tpl, evidence_type_hint = _PRO_TEMPLATES[idx]
            stance_hint = "pro"
            stance_label = "支持方向"
        else:
            tpl, evidence_type_hint = _CON_TEMPLATES[idx]
            stance_hint = "con"
            stance_label = "反对方向"
        kind = "answer" if origin == "zhihu" else "page"
        results.append(
            NormalizedSearchResult(
                origin=origin,
                kind=kind,
                title=f"【演示数据】「{topic}」的{stance_label}（{i + 1}）",
                url=_demo_url(
                    origin,
                    i + 1,
                    query,
                    stance_hint=stance_hint,
                    evidence_type_hint=evidence_type_hint,
                ),
                author="演示用户（非真实作者）",
                summary=tpl.format(t=topic),
                published_at="",
                vote_count=0,
                comment_count=0,
                stance_hint=stance_hint,
                evidence_type_hint=evidence_type_hint,
                is_demo_source=True,
            )
        )
    return results


class MockZhihuProvider:
    """知乎官方接口的 Mock 实现（演示模式）。"""

    is_demo = True

    async def search(self, query: str, limit: int = 10) -> list[NormalizedSearchResult]:
        return _demo_results(query, "zhihu", min(limit, 8))

    async def hot_list(self, limit: int = 20) -> list[NormalizedHotItem]:
        count = min(limit, 8)
        return [
            NormalizedHotItem(
                title=f"【演示数据】热榜界面示意条目 {i + 1}（非真实热点）",
                url=_demo_url("hot", i + 1, f"mock-hot-item-{i + 1}"),
                heat=0,
                excerpt="演示模式未连接真实热榜；此条目只用于展示界面，不代表任何平台热度或现实话题。",
            )
            for i in range(count)
        ]

    async def direct_answer(self, question: str) -> NormalizedDirectAnswer:
        topic = _topic(question)
        return NormalizedDirectAnswer(
            answer=f"【演示数据】演示模式未检索真实来源，不能提供关于「{topic}」的事实性回答；以下内容仅用于展示审理流程。",
            key_concepts=["待由真实来源界定的核心概念（演示）"],
            disputes=["待取得真实来源后识别争议（演示）"],
            sub_questions=[f"应使用哪些可观测指标核验「{topic}」？（演示）"],
        )


class MockWebSearchProvider:
    """全网搜索的 Mock 实现（演示模式）。"""

    is_demo = True

    async def search(self, query: str, limit: int = 8) -> list[NormalizedSearchResult]:
        return _demo_results(query, "web", min(limit, 6))
