"""Cross Examiner：检查双方论证的逻辑与证据问题（规则 + LLM 双轨）。"""
from __future__ import annotations

from typing import Any

from app.agents.base import clean_ids, clean_text, llm_json
from app.models import Argument, Evidence, Source
from app.prompts import templates as T

ISSUE_KEYS = {
    "concept_swap": "偷换概念",
    "causal_inversion": "因果倒置",
    "correlation_not_causation": "相关不等于因果",
    "overgeneralization": "过度泛化",
    "sample_bias": "样本偏差",
    "survivorship_bias": "幸存者偏差",
    "insufficient_evidence": "证据不足",
    "recency_risk": "证据时效性风险",
    "scope_mismatch": "适用范围错误",
    "definition_conflict": "定义冲突",
    "evidence_argument_mismatch": "论点与证据不匹配",
    "missing_counterexample": "缺乏反例",
    "source_concentration": "证据来源过于集中",
    "data_quality": "数据质量问题",
    "opinion_as_fact": "观点被当作事实",
    "prediction_as_fact": "预测被当作事实",
}


def _heuristic_cross(
    pro_args: list[Argument], defense_args: list[Argument], evidence: list[Evidence], sources: list[Source]
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    ev_by_id = {e.id: e for e in evidence}
    src_by_id = {s.id: s for s in sources}

    def check_side(side_args: list[Argument], side: str) -> None:
        cited: list[Evidence] = []
        for a in side_args:
            cited.extend(ev_by_id[e] for e in a.evidence_ids if e in ev_by_id)
        if not side_args:
            issues.append(
                {
                    "target_side": side,
                    "issue_type": "insufficient_evidence",
                    "severity": "high",
                    "description": f"{'控方' if side == 'prosecution' else '辩方'}未能基于证据构建任何论点。",
                    "related_evidence_ids": [],
                }
            )
            return
        # 观点/预测被当作事实使用
        for a in side_args:
            soft = [ev_by_id[e] for e in a.evidence_ids if e in ev_by_id and ev_by_id[e].evidence_type in ("opinion", "prediction", "assumption")]
            if soft and len(soft) >= max(1, len(a.evidence_ids)):
                issues.append(
                    {
                        "target_side": side,
                        "issue_type": "opinion_as_fact",
                        "severity": "medium",
                        "description": f"论点「{a.title}」主要依赖观点/预测类证据（如 {soft[0].id}），确定性被高估。",
                        "related_evidence_ids": [s.id for s in soft][:4],
                    }
                )
        # 来源集中度
        src_counts: dict[str, int] = {}
        for ev in cited:
            src_counts[ev.source_id] = src_counts.get(ev.source_id, 0) + 1
        if cited and src_counts:
            top_src, top_n = max(src_counts.items(), key=lambda kv: kv[1])
            if top_n / len(cited) > 0.6 and len(src_counts) < 3:
                s = src_by_id.get(top_src)
                issues.append(
                    {
                        "target_side": side,
                        "issue_type": "source_concentration",
                        "severity": "medium",
                        "description": f"该方证据 {top_n}/{len(cited)} 来自同一来源（{s.title[:40] if s else top_src}），缺少独立来源交叉验证。",
                        "related_evidence_ids": [e.id for e in cited if e.source_id == top_src][:4],
                    }
                )
        # 时效性
        stale = [e for e in cited if (e.source_id in src_by_id) and (src_by_id[e.source_id].published_at or "")[:4].isdigit() and int(src_by_id[e.source_id].published_at[:4]) <= 2022]
        if stale:
            issues.append(
                {
                    "target_side": side,
                    "issue_type": "recency_risk",
                    "severity": "low",
                    "description": f"该方引用了 {len(stale)} 条较早发布的证据，需注意时效性。",
                    "related_evidence_ids": [e.id for e in stale][:4],
                }
            )
        # 缺少反例讨论
        own = "pro" if side == "prosecution" else "con"
        opposing = [e for e in evidence if e.stance == ("con" if own == "pro" else "pro")]
        if opposing and not any(e.stance != own for e in cited):
            issues.append(
                {
                    "target_side": side,
                    "issue_type": "missing_counterexample",
                    "severity": "medium",
                    "description": "该方论证未正面处理任何相反方向的证据。",
                    "related_evidence_ids": [e.id for e in opposing[:3]],
                }
            )

    check_side(pro_args, "prosecution")
    check_side(defense_args, "defense")
    return issues[:8]


async def run_cross_examiner(
    proposition: str,
    pro_args: list[Argument],
    defense_args: list[Argument],
    evidence: list[Evidence],
    sources: list[Source],
    *,
    mode: str,
) -> tuple[list[dict[str, Any]], str]:
    if mode == "llm":
        def fmt_args(args: list[Argument]) -> str:
            return "\n".join(
                f"- {a.title}（引用：{','.join(a.evidence_ids) or '无'}）：{a.body[:200]}" for a in args
            ) or "（无）"

        data = await llm_json(
            T.CROSS_EXAM_SYSTEM,
            T.fill(
                T.CROSS_EXAM_USER,
                proposition=proposition,
                pro_arguments=fmt_args(pro_args),
                defense_arguments=fmt_args(defense_args),
            ),
            max_tokens=2500,
        )
        if isinstance(data, dict) and isinstance(data.get("cross_examinations"), list):
            valid_ids = {e.id for e in evidence}
            out = []
            for item in data["cross_examinations"]:
                if not isinstance(item, dict):
                    continue
                it = item.get("issue_type")
                description = clean_text(item.get("description"), 600)
                if not description:
                    continue
                out.append(
                    {
                        "target_side": item.get("target_side") if isinstance(item.get("target_side"), str) and item["target_side"] in ("prosecution", "defense", "both") else "both",
                        "issue_type": it if isinstance(it, str) and it in ISSUE_KEYS else "other",
                        "severity": item.get("severity") if isinstance(item.get("severity"), str) and item["severity"] in ("low", "medium", "high") else "medium",
                        "description": description,
                        "related_evidence_ids": clean_ids(item.get("related_evidence_ids"), valid_ids, 6),
                    }
                )
            out = out[:8]
            if out:
                return out, "llm"
    return _heuristic_cross(pro_args, defense_args, evidence, sources), "heuristic"
