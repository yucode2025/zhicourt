"""Challenger：处理用户质询（分类 + 基于现有证据回应，不编造新证据）。"""
from __future__ import annotations

import json
from typing import Any

from app.agents.base import clean_ids, clean_text, llm_json
from app.models import Evidence
from app.prompts import templates as T

CHALLENGE_TYPES = {"fact", "logic", "evidence", "definition", "scope", "source", "other"}


def _classify_by_keyword(text: str) -> str:
    rules = [
        ("definition", ["定义", "概念", "什么意思", "口径", "指的"]),
        ("source", ["来源", "出处", "可信吗", "权威", "谁说的"]),
        ("scope", ["范围", "适用", "仅限", "片面", "以偏概全"]),
        ("logic", ["逻辑", "因果", "倒置", "矛盾", "偷换"]),
        ("evidence", ["证据", "样本", "数据可靠", "不够", "不足"]),
        ("fact", ["事实", "真的吗", "实际", "真实"]),
    ]
    for t, words in rules:
        if any(w in text for w in words):
            return t
    return "other"


def heuristic_challenge(
    target: str, text: str, evidence: list[Evidence]
) -> dict[str, Any]:
    ctype = _classify_by_keyword(text)
    top = sorted((e for e in evidence if not getattr(getattr(e, "source", None), "is_demo", False)), key=lambda e: e.strength, reverse=True)[:3]
    related = [e.id for e in top]
    rel_desc = "；".join(f"「{e.claim[:40]}」（可信度 {e.strength}）" for e in top) or "当前没有可用于现实判断的真实证据"
    target_name = {
        "prosecution": "控方论证", "defense": "辩方论证", "judge": "法官判断",
        "evidence": "该条证据", "source": "该来源",
    }.get(target, "相关内容")

    type_labels = {
        "fact": "事实质疑", "logic": "逻辑质疑", "evidence": "证据质疑", "definition": "定义质疑",
        "scope": "范围质疑", "source": "来源质疑", "other": "一般质询",
    }
    response = (
        f"您的质询属于「{type_labels[ctype]}」。\n\n"
        f"针对{target_name}，当前案件内可追溯的相关证据为：{rel_desc}。\n\n"
    )
    if not top:
        response += "此案仅有演示条目或缺少真实证据，不能确认论点有据可查；请补充可核验的独立来源。"
    elif ctype in ("evidence", "source"):
        response += (
            "质询成立与否取决于这些证据的独立性与口径：若其来源集中或时效不足，则该论证强度应下调；"
            "本系统的证据可信度评分已包含来源独立性与时效权重，可在证据图页查看每条证据的评分构成。"
        )
    elif ctype == "definition":
        response += (
            "定义质疑通常是最有力的质询类型：双方部分分歧确实可能源于同一术语的不同口径。"
            "判决书中已将定义冲突单列，请您对照'核心分歧'部分确认该质询是否已被覆盖。"
        )
    elif ctype == "logic":
        response += (
            "请对照质证记录中列出的逻辑问题（如因果方向、过度泛化等）。若您指出的逻辑问题未被覆盖，"
            "说明该论点的证据链条可能比展示的更弱。"
        )
    else:
        response += (
            "基于现有证据，该内容整体有据可查；但证据之间存在口径与独立性差异，结论应理解为条件性判断而非绝对事实。"
        )

    return {"challenge_type": ctype, "response": response[:1500], "related_evidence_ids": related}


async def run_challenger(
    proposition: str, target: str, text: str, evidence: list[Evidence], *, mode: str, focus: str = ""
) -> tuple[dict[str, Any], str]:
    # 路由已将 evidence 缩到目标焦点；这里再限量，确保提示词和回包都无法越界。
    focused_evidence = list(evidence[:20])
    if mode == "llm" and any(
        not getattr(getattr(item, "source", None), "is_demo", False)
        for item in focused_evidence
    ):
        evidence_payload = [
            {
                "id": item.id,
                "claim": item.claim[:200],
                "type": item.evidence_type,
                "strength": item.strength,
            }
            for item in focused_evidence
        ]
        payload = json.dumps(
            {
                "proposition": proposition[:300],
                "target": target,
                "focus": focus[:1500],
                "question": text[:800],
                "focused_evidence_ids": [item.id for item in focused_evidence],
                "evidence": evidence_payload,
            },
            ensure_ascii=False,
        )
        data = await llm_json(
            T.CHALLENGER_SYSTEM,
            T.fill(T.CHALLENGER_USER, payload=payload),
            max_tokens=1200,
        )
        if isinstance(data, dict) and clean_text(data.get("response"), 1500):
            valid_ids = {item.id for item in focused_evidence}
            ct = str(data.get("challenge_type", "other"))
            return {
                "challenge_type": ct if ct in CHALLENGE_TYPES else "other",
                "response": clean_text(data["response"], 1500),
                "related_evidence_ids": clean_ids(data.get("related_evidence_ids"), valid_ids, 4),
            }, "llm"
    return heuristic_challenge(target, text, focused_evidence), "heuristic"
