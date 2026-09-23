"""Case Planner：分析问题 → 审理命题 + 检索方向。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.agents.base import clean_list, clean_text, llm_json
from app.prompts import templates as T

# 保守门禁：只有明确的单一答案/操作/闲聊请求才拒绝；混合问题优先审理。
_DEBATE_SIGNALS = ("是否", "会不会", "应不应该", "应该", "值得", "利弊", "影响", "为什么", "如何看待", "怎么看", "合理", "更好", "前景", "争议", "能否", "可能", "真假", "可靠吗", "成立")
_REASONS = {
    "calculation": "这是可直接计算的问题，更适合计算工具。",
    "temporal_fact": "这是查询单一时间事实的问题，更适合直接检索。",
    "operation": "这是操作指令，更适合直接执行或教程。",
    "chat": "这是闲聊，不包含可审理的判断。",
    "invalid": "请输入清晰、具体的审理问题。",
}


@dataclass
class TrialPlan:
    title: str = ""
    proposition: str = ""
    key_concepts: list[str] = field(default_factory=list)
    disputes: list[str] = field(default_factory=list)
    sub_questions: list[str] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    suitable: bool = True
    needs_rewrite: bool = False
    mode: str = "heuristic"  # llm | heuristic
    background: str = ""  # 直答仅是未核验的检索背景，不构成证据
    input_classification: str = "debatable"
    rejection_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "proposition": self.proposition,
            "key_concepts": self.key_concepts,
            "disputes": self.disputes,
            "sub_questions": self.sub_questions,
            "search_queries": self.search_queries,
            "suitable": self.suitable,
            "needs_rewrite": self.needs_rewrite,
            "mode": self.mode,
            "input_classification": self.input_classification,
            "rejection_reason": self.rejection_reason,
        }


def classify_input(question: str) -> tuple[str, str | None]:
    q = question.strip().strip("？?。.!！ ")
    q = re.sub(r"^(请问|请帮我|帮我|请|麻烦你)\s*", "", q)
    if len(q) < 2 or not re.search(r"[\w\u4e00-\u9fff]", q):
        kind = "invalid"
    elif any(signal in q for signal in _DEBATE_SIGNALS):
        kind = "debatable"
    elif re.fullmatch(r"[\d\s+*/×÷().（）％%]+(?:等于多少|是多少|结果)?", q) or re.fullmatch(r"(?:计算|算一下|算算)\s*[\d\s+*/×÷().（）％%]+", q):
        kind = "calculation"
    elif re.fullmatch(r"(?:你好|嗨|早上好|晚上好|谢谢|再见|你是谁|讲个笑话)", q):
        kind = "chat"
    elif re.match(r"^(?:怎么|如何)(?:安装|打开|下载|注册|登录|设置|操作|使用|部署|运行|删除|复制)", q):
        kind = "operation"
    elif re.fullmatch(r".*(?:哪年|哪一年|什么时候|几月几日|哪天|哪日|具体日期|出生于哪年|成立于哪年|在哪里|是哪里|有多高|有多远|是谁|是什么)", q):
        kind = "temporal_fact"
    else:
        kind = "debatable"
    return kind, _REASONS.get(kind)


def _looks_like_fact_query(question: str) -> bool:
    return classify_input(question)[0] != "debatable"


_REQUEST_PREFIX_RE = re.compile(
    r"^(?:请帮忙|请帮我|请问|麻烦|帮我看看|帮我分析|帮我|大家觉得|你觉得|我想知道|请)?"
    r"(?:分析|看看|评估|判断)?(?:一下)?[：:]?\s*"
)


def _heuristic_plan(question: str) -> TrialPlan:
    q = question.strip().rstrip("？?。.")
    core = _REQUEST_PREFIX_RE.sub("", q).strip() or q

    kind, reason = classify_input(question)
    if kind != "debatable":
        return TrialPlan(
            title=question[:200],
            input_classification=kind,
            rejection_reason=reason,
            proposition=core,
            suitable=False,
            needs_rewrite=False,
            key_concepts=[],
            disputes=[],
            sub_questions=[],
            search_queries=[core],
        )

    proposition = core if core.endswith(("吗", "么")) or ("是否" in core) or ("会不会" in core) or ("能不能" in core) else f"{core}是否成立"
    # 统一为陈述式判断句：去掉问句助词
    prop = proposition.rstrip("？?")
    if prop.endswith("吗"):
        prop = prop[:-1] + "，这一判断是否成立"
    plan = TrialPlan(
        title=question[:200],
        proposition=prop,
        key_concepts=[],
        disputes=[],
        sub_questions=[f"判断「{core[:24]}」需要哪些可观测的证据？"],
        search_queries=[core, f"{core} 影响 数据", f"{core} 反例 争议", f"{core} 专家 观点"],
        suitable=True,
        needs_rewrite=True,
    )
    return plan


async def run_planner(question: str) -> TrialPlan:
    kind, reason = classify_input(question)
    if kind != "debatable":
        return _heuristic_plan(question)
    data = await llm_json(
        T.PLANNER_SYSTEM, T.fill(T.PLANNER_USER, question=question), max_tokens=1200
    )
    if isinstance(data, dict) and clean_text(data.get("proposition"), 200):
        return TrialPlan(
            title=question[:200],
            proposition=clean_text(data["proposition"], 200),
            key_concepts=clean_list(data.get("key_concepts"), 8, 50),
            disputes=clean_list(data.get("disputes"), 6, 120),
            sub_questions=clean_list(data.get("sub_questions"), 6, 120),
            search_queries=clean_list(data.get("search_queries"), 6, 60),
            # 适审性只由前置保守分类决定，避免 LLM 与 plan/create/start 门禁分叉。
            suitable=True,
            needs_rewrite=data.get("needs_rewrite") if isinstance(data.get("needs_rewrite"), bool) else True,
            mode="llm",
            input_classification="debatable",
            rejection_reason=None,
        )
    return _heuristic_plan(question)
