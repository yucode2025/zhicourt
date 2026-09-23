"""Research Agent：生成知乎 / 全网检索查询词。"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.base import clean_list, llm_json
from app.agents.planner import TrialPlan
from app.prompts import templates as T


@dataclass
class ResearchPlan:
    zhihu_queries: list[str] = field(default_factory=list)
    web_queries: list[str] = field(default_factory=list)
    mode: str = "heuristic"


def _variants(proposition: str, concepts: list[str]) -> ResearchPlan:
    core = proposition.strip().rstrip("？?。.，,")
    for tail in ("是否成立", "这一判断是否成立", "是否"):
        core = core.replace(tail, "").strip()
    base = core[:40]
    concept = concepts[0][:16] if concepts else ""
    return ResearchPlan(
        zhihu_queries=[
            base,
            f"{base} 真实经历" if not concept else f"{concept} 经历 讨论",
            f"{base} 为什么 争议",
        ],
        web_queries=[
            f"{base} 报告 数据",
            f"{base} 研究 分析",
            f"{base} 预测 未来",
        ],
    )


async def run_research(plan: TrialPlan) -> ResearchPlan:
    data = await llm_json(
        T.RESEARCH_SYSTEM,
        T.fill(
            T.RESEARCH_USER,
            proposition=plan.proposition,
            concepts="、".join(plan.key_concepts) or "无",
            disputes=("、".join(plan.disputes) or "无") + (f"\n未经核验的直答背景（仅用于检索，不是证据）：{plan.background[:800]}" if plan.background else ""),
        ),
        max_tokens=800,
    )
    if isinstance(data, dict) and (clean_list(data.get("zhihu_queries"), 5, 80) or clean_list(data.get("web_queries"), 5, 80)):
        return ResearchPlan(
            zhihu_queries=clean_list(data.get("zhihu_queries"), 5, 80),
            web_queries=clean_list(data.get("web_queries"), 5, 80),
            mode="llm",
        )
    fallback = _variants(plan.proposition, plan.key_concepts)
    if plan.background:
        # 无 LLM 时也让直答进入实际检索，限长且不写入 Evidence。
        fallback.zhihu_queries[-1] = f"{plan.proposition[:30]} {plan.background[:35]}"
    return fallback
