"""Verdict Writer：将 Judge 结构化结果撰写为《知识判决书》（不得改变事实含义）。"""
from __future__ import annotations

from typing import Any

from app.agents.base import clean_text, llm_json
from app.prompts import templates as T

import json


def heuristic_writer(judge: dict[str, Any], pro_summary: str, defense_summary: str, cross_summary: str) -> dict[str, str]:
    return {
        "summary": clean_text(judge.get("conclusion"), 1500),
        "prosecution_summary": pro_summary[:200],
        "defense_summary": defense_summary[:200],
        "cross_exam_summary": cross_summary[:300],
    }


async def run_verdict_writer(
    judge: dict[str, Any],
    pro_summary: str,
    defense_summary: str,
    cross_summary: str,
    *,
    mode: str,
) -> tuple[dict[str, str], str]:
    if mode == "llm":
        data = await llm_json(
            T.VERDICT_WRITER_SYSTEM,
            T.fill(T.VERDICT_WRITER_USER, judge=json.dumps(judge, ensure_ascii=False)[:6000]),
            max_tokens=1200,
        )
        if isinstance(data, dict) and any(clean_text(data.get(k), 300) for k in ("prosecution_summary", "defense_summary", "cross_exam_summary")):
            return {
                "summary": clean_text(judge.get("conclusion"), 1500),
                # Writer 只做展示层整理；canonical 控辩与质证摘要不允许被模型改写成新事实。
                "prosecution_summary": pro_summary[:300],
                "defense_summary": defense_summary[:300],
                "cross_exam_summary": cross_summary[:500],
            }, "llm"
    return heuristic_writer(judge, pro_summary, defense_summary, cross_summary), "heuristic"
