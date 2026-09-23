"""Judge Agent：基于 Evidence Engine 的结构化裁决（不是角色表演）。"""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from app.agents.base import clean_list, clean_score, clean_text, llm_json
from app.models import Argument, CrossExamination, Evidence
from app.prompts import templates as T


_DECISIVE_STANCES = {
    "prosecution": ("pro", "con"),
    "defense": ("con", "pro"),
}
_NEGATION_MARKERS = ("不", "未", "无", "没有", "并非", "不会", "不能", "否认", "反对")


def _claim_key(claim: str) -> str:
    return re.sub(r"[\W_]+", "", claim.casefold())


def _claims_overlap(first: Evidence, second: Evidence) -> bool:
    """过滤只差标点的重复主张，同时保留具有明确否定关系的反例。"""
    first_key = _claim_key(first.claim)
    second_key = _claim_key(second.claim)
    if not first_key or not second_key:
        return False
    first_negated = any(marker in first_key for marker in _NEGATION_MARKERS)
    second_negated = any(marker in second_key for marker in _NEGATION_MARKERS)
    if first_negated != second_negated:
        return False
    return first_key == second_key


def _source_tokens(item: Evidence) -> set[tuple[str, str]]:
    """返回来源 hostname/发布主体标识；绝不使用每条搜索结果的 source_id。"""
    source = getattr(item, "source", None)
    if source is None:
        return {("unknown", "")}
    try:
        hostname = (urlsplit(source.url or "").hostname or "").rstrip(".").casefold()
    except ValueError:
        hostname = ""
    subject = re.sub(r"\s+", " ", (source.author or "").strip().casefold())
    tokens = set()
    if hostname:
        tokens.add(("hostname", hostname))
    if subject:
        tokens.add(("subject", subject))
    return tokens or {("origin", (source.origin or "unknown").strip().casefold())}


def _source_group_ids(evidence: list[Evidence]) -> list[int]:
    """共享 hostname 或主体的结果属于同一独立来源（含传递合并）。"""
    parents = list(range(len(evidence)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    seen: dict[tuple[str, str], int] = {}
    for index, item in enumerate(evidence):
        for token in _source_tokens(item):
            if token in seen:
                union(index, seen[token])
            else:
                seen[token] = index
    roots: dict[int, int] = {}
    result: list[int] = []
    for index in range(len(evidence)):
        root = find(index)
        result.append(roots.setdefault(root, len(roots)))
    return result


def _select_verdict_evidence(
    evidence: list[Evidence], conclusion_stance: str, *, limit: int = 4
) -> tuple[list[str], list[str], bool]:
    """按裁决方向从强到弱选取支持与反例证据，排除 neutral 和语义重复。"""
    mapping = _DECISIVE_STANCES.get(conclusion_stance)
    if mapping is None:
        return [], [], True
    support_stance, counter_stance = mapping
    ranked = sorted(
        (item for item in evidence if item.strength > 0 and not getattr(getattr(item, "source", None), "is_demo", False)),
        key=lambda item: item.strength, reverse=True,
    )

    def take_unique(stance: str, excluded: list[Evidence]) -> list[Evidence]:
        selected: list[Evidence] = []
        for item in ranked:
            if item.stance != stance:
                continue
            if any(_claims_overlap(item, other) for other in excluded + selected):
                continue
            selected.append(item)
            if len(selected) == limit:
                break
        return selected

    support_items = take_unique(support_stance, [])
    counter_items = take_unique(counter_stance, support_items)
    complete = bool(support_items and counter_items)
    return [item.id for item in support_items], [item.id for item in counter_items], complete


def _sanitize_verdict_evidence(
    data: dict[str, Any], evidence: list[Evidence], conclusion_stance: str, *, limit: int = 4
) -> tuple[list[str], list[str], bool]:
    """清洗 LLM strongest，并按正确立场补齐遗漏项。"""
    fallback_support, fallback_counter, _ = _select_verdict_evidence(
        evidence, conclusion_stance, limit=limit
    )
    mapping = _DECISIVE_STANCES.get(conclusion_stance)
    if mapping is None:
        return [], [], True
    support_stance, counter_stance = mapping
    by_id = {item.id: item for item in evidence if item.strength > 0 and not getattr(getattr(item, "source", None), "is_demo", False)}

    def valid_ids(raw: Any, stance: str, excluded: list[Evidence]) -> list[str]:
        if not isinstance(raw, list):
            return []
        selected: list[Evidence] = []
        for item_id in raw:
            item = by_id.get(item_id) if isinstance(item_id, str) else None
            if item is None or item.stance != stance or item in selected:
                continue
            if any(_claims_overlap(item, other) for other in excluded + selected):
                continue
            selected.append(item)
        return [item.id for item in selected[:limit]]

    support = valid_ids(data.get("strongest_evidence_ids"), support_stance, [])
    for item_id in fallback_support:
        item = by_id[item_id]
        selected = [by_id[selected_id] for selected_id in support]
        if len(support) == limit or any(_claims_overlap(item, other) for other in selected):
            continue
        support.append(item_id)

    support_items = [by_id[item_id] for item_id in support]
    counter = valid_ids(data.get("strongest_counter_evidence_ids"), counter_stance, support_items)
    for item_id in fallback_counter:
        item = by_id[item_id]
        selected = [by_id[selected_id] for selected_id in counter]
        if len(counter) == limit or any(_claims_overlap(item, other) for other in support_items + selected):
            continue
        counter.append(item_id)
    return support, counter, bool(support and counter)


def _clean_strongest_ids(
    conclusion_stance: str,
    evidence: list[Evidence],
    strongest_ids: Any,
    counter_ids: Any,
    *,
    limit: int = 4,
) -> tuple[list[str], list[str]]:
    support, counter, _ = _sanitize_verdict_evidence(
        {
            "strongest_evidence_ids": strongest_ids,
            "strongest_counter_evidence_ids": counter_ids,
        },
        evidence,
        conclusion_stance,
        limit=limit,
    )
    return support, counter


def heuristic_judge(
    proposition: str,
    pro_args: list[Argument],
    defense_args: list[Argument],
    evidence: list[Evidence],
    cross_exams: list[CrossExamination],
    disputes: list[str],
    sub_questions: list[str],
) -> dict[str, Any]:
    # 论证由证据派生，不再次并入分数；同发布主体最多提供一次独立权重。
    real_evidence = [e for e in evidence if e.strength > 0 and not getattr(getattr(e, "source", None), "is_demo", False)]

    source_groups = _source_group_ids(real_evidence)

    def side_avg(stance: str) -> float:
        by_source: dict[int, float] = {}
        for ev, group_id in zip(real_evidence, source_groups, strict=True):
            if ev.stance == stance:
                by_source[group_id] = max(by_source.get(group_id, 0.0), ev.strength)
        return sum(by_source.values()) / len(by_source) if by_source else 0.0

    def penalty(side: str) -> float:
        weights = {"low": 0.01, "medium": 0.035, "high": 0.08}
        return min(0.25, sum(weights.get(c.severity, 0.035) for c in cross_exams if c.target_side in (side, "both")))

    pro_score = side_avg("pro")
    con_score = side_avg("con")
    diff = (pro_score - penalty("prosecution")) - (con_score - penalty("defense"))
    _, _, complete = _select_verdict_evidence(real_evidence, "prosecution")
    distinct_sources = set(source_groups)
    all_demo = bool(evidence) and all(getattr(getattr(e, "source", None), "is_demo", False) for e in evidence)

    if not distinct_sources:
        stance = "insufficient"
        conclusion = "当前仅有演示来源或无可追溯的真实证据，无法据此裁决现实命题。请补充独立真实来源。"
        confidence = 0.1 if all_demo else 0.15
    elif len(distinct_sources) < 3:
        stance = "insufficient"
        conclusion = (
            "现有证据数量与独立性不足以支持可靠判断。当前双方论证均依赖有限来源，"
            "无法排除关键反例。建议补充更多独立来源后再作判断。"
        )
        confidence = 0.2
    elif not complete:
        stance = "insufficient"
        conclusion = "当前证据缺少可区分的支持与反对方向，不能据此作出倾向性裁决。请补充独立反例。"
        confidence = 0.2
    elif abs(diff) < 0.06:
        stance = "conditional"
        conclusion = (
            "双方论证强度接近，当前证据不足以给出倾向性结论。判断高度依赖于概念口径与适用范围："
            "在不同定义与时间范围内，结论可能不同。"
        )
        confidence = 0.45
    elif diff > 0:
        stance = "prosecution"
        conclusion = (
            "综合证据可信度与质证情况，支持命题一方的证据整体更强、来源相对独立，"
            "当前更合理的判断倾向于支持命题；但反例并非无效，结论应在限定条件下理解。"
        )
        confidence = min(0.75, 0.5 + abs(diff))
    else:
        stance = "defense"
        conclusion = (
            "综合证据可信度与质证情况，反对命题一方的证据整体更强，"
            "当前更合理的判断倾向于反对命题；正方证据仍构成有效压力，结论应保持开放。"
        )
        confidence = min(0.75, 0.5 + abs(diff))

    shared_raw = [
        e.claim[:120]
        for e in real_evidence
        if e.evidence_type in ("fact", "data") and e.stance == "neutral"
        and any(e.id in (a.evidence_ids or []) for a in pro_args)
        and any(e.id in (a.evidence_ids or []) for a in defense_args)
    ]
    shared = list(dict.fromkeys(shared_raw))[:4]

    ranked = sorted(real_evidence, key=lambda e: e.strength, reverse=True)
    pro_ranked = [e.id for e in ranked if e.stance == "pro"]
    con_ranked = [e.id for e in ranked if e.stance == "con"]
    strongest_ids, counter_ids = _clean_strongest_ids(
        stance,
        real_evidence,
        pro_ranked if stance == "prosecution" else con_ranked,
        con_ranked if stance == "prosecution" else pro_ranked,
        limit=3,
    )

    return {
        "conclusion": conclusion,
        "conclusion_stance": stance,
        "confidence": round(confidence, 2),
        "shared_facts": shared,
        "core_disputes": disputes[:4],
        "strongest_evidence_ids": strongest_ids,
        "strongest_counter_evidence_ids": counter_ids,
        "evidence_gaps": [
            "缺少独立第三方数据的交叉验证",
            "缺少针对不同时间范围/口径的分层证据",
        ],
        "definition_conflicts": [c.description[:200] for c in cross_exams if c.issue_type in ("definition_conflict", "concept_swap")][:4],
        "unknowns": ["仅按来源摘要与规则提取，未调用 LLM 精读；因果关系仍需核验。"],
        "verdict_changers": [
            "若出现多来源一致的纵向数据，可显著提高结论置信度",
            "若出现直接反驳当前最强证据的独立反例，结论可能反转",
        ],
        "next_questions": (sub_questions[:3] or ["该命题依赖哪些可观测指标？"]) + ["现有证据的时间跨度是否足够？"],
    }


async def run_judge(
    proposition: str,
    pro_args: list[Argument],
    defense_args: list[Argument],
    evidence: list[Evidence],
    cross_exams: list[CrossExamination],
    disputes: list[str],
    sub_questions: list[str],
    *,
    mode: str,
) -> tuple[dict[str, Any], str]:
    real_evidence = [e for e in evidence if e.strength > 0 and not getattr(getattr(e, "source", None), "is_demo", False)]
    if len(set(_source_group_ids(real_evidence))) < 3:
        return heuristic_judge(proposition, pro_args, defense_args, evidence, cross_exams, disputes, sub_questions), "heuristic"
    if mode == "llm":
        def fmt_args(args: list[Argument]) -> str:
            return "\n".join(f"- {a.title}：{a.body[:150]}（证据：{','.join(a.evidence_ids) or '无'}）" for a in args) or "（无）"

        fmt_ev = "\n".join(f"{e.id}: {e.claim[:120]} [{e.evidence_type}/{e.stance}/强度{e.strength}]" for e in evidence) or "（无）"
        fmt_cross = "\n".join(f"- [{c.issue_type}/{c.severity}] {c.description[:150]}" for c in cross_exams) or "（无）"

        data = await llm_json(
            T.JUDGE_SYSTEM,
            T.fill(
                T.JUDGE_USER,
                proposition=proposition,
                pro_arguments=fmt_args(pro_args),
                defense_arguments=fmt_args(defense_args),
                cross_exams=fmt_cross,
                evidence=fmt_ev,
            ),
            max_tokens=3000,
        )
        if isinstance(data, dict) and clean_text(data.get("conclusion"), 1500):
            stance = str(data.get("conclusion_stance", "conditional"))
            if stance not in ("prosecution", "defense", "conditional", "insufficient"):
                stance = "conditional"
            strongest_ids, counter_ids, complete = _sanitize_verdict_evidence(data, real_evidence, stance)
            unsupported = stance in _DECISIVE_STANCES and not complete
            if unsupported:
                stance = "insufficient"
                strongest_ids = counter_ids = []
            confidence = clean_score(data.get("confidence"))
            if stance == "insufficient":
                confidence = min(confidence, 0.2)
            return {
                "conclusion": "证据方向不完整，无法作出倾向性裁决；请补充独立反例。" if unsupported else clean_text(data["conclusion"], 1500),
                "conclusion_stance": stance,
                "confidence": round(confidence, 2),
                "shared_facts": [x for x in clean_list(data.get("shared_facts"), 6, 200) if any(x in e.claim or x in e.summary for e in real_evidence if e.stance == "neutral" and e.evidence_type in ("fact", "data") and any(e.id in (a.evidence_ids or []) for a in pro_args) and any(e.id in (a.evidence_ids or []) for a in defense_args))],
                "core_disputes": clean_list(data.get("core_disputes"), 6, 200),
                "strongest_evidence_ids": strongest_ids,
                "strongest_counter_evidence_ids": counter_ids,
                "evidence_gaps": clean_list(data.get("evidence_gaps"), 6, 200),
                "definition_conflicts": [x for x in clean_list(data.get("definition_conflicts"), 6, 200) if any(x in c.description or c.description in x for c in cross_exams if c.issue_type in ("definition_conflict", "concept_swap"))],
                "unknowns": clean_list(data.get("unknowns"), 6, 300),
                "verdict_changers": clean_list(data.get("verdict_changers"), 6, 200),
                "next_questions": clean_list(data.get("next_questions"), 6, 120),
            }, "llm"
    return (
        heuristic_judge(proposition, pro_args, defense_args, evidence, cross_exams, disputes, sub_questions),
        "heuristic",
    )
