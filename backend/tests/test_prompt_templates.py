"""Prompt 模板回归：JSON 示例与占位符替换零冲突（真实 LLM 模式曾因 str.format 崩溃）。"""
from __future__ import annotations

import json

from app.prompts import templates as T
from app.services.llm.gateway import _extract_json


def _renderable_templates():
    return [
        ("PLANNER_SYSTEM", T.PLANNER_SYSTEM, {}),
        ("PLANNER_USER", T.PLANNER_USER, {"question": "AI 会不会淘汰程序员？"}),
        ("RESEARCH_SYSTEM", T.RESEARCH_SYSTEM, {}),
        ("RESEARCH_USER", T.RESEARCH_USER, {"proposition": "p", "concepts": "c", "disputes": "d"}),
        ("EVIDENCE_SYSTEM", T.EVIDENCE_SYSTEM, {}),
        ("EVIDENCE_USER", T.EVIDENCE_USER, {"proposition": "p", "sources": "s", "max_evidence": "4"}),
        ("ARGUMENT_SYSTEM", T.ARGUMENT_SYSTEM, {"role": "Prosecutor", "role_desc": "desc"}),
        ("ARGUMENT_USER", T.ARGUMENT_USER, {"proposition": "p", "evidence": "e"}),
        ("CROSS_EXAM_SYSTEM", T.CROSS_EXAM_SYSTEM, {}),
        ("CROSS_EXAM_USER", T.CROSS_EXAM_USER, {"proposition": "p", "pro_arguments": "a", "defense_arguments": "b"}),
        ("JUDGE_SYSTEM", T.JUDGE_SYSTEM, {}),
        ("JUDGE_USER", T.JUDGE_USER, {"proposition": "p", "pro_arguments": "a", "defense_arguments": "b",
                                       "cross_exams": "c", "evidence": "e"}),
        ("VERDICT_WRITER_SYSTEM", T.VERDICT_WRITER_SYSTEM, {}),
        ("VERDICT_WRITER_USER", T.VERDICT_WRITER_USER, {"judge": "j"}),
        ("CHALLENGER_SYSTEM", T.CHALLENGER_SYSTEM, {}),
        ("CHALLENGER_USER", T.CHALLENGER_USER, {"payload": '{"target":"judge","question":"t"}'}),
    ]


def test_all_templates_render_without_keyerror():
    """曾因 str.format 解析 JSON 大括号抛 KeyError: '"claims"'。"""
    for name, tpl, kw in _renderable_templates():
        rendered = T.fill(tpl, **kw)  # 不得抛出任何异常
        assert "«" not in rendered, f"{name} 有未替换的占位符"
        assert "»" not in rendered, f"{name} 有未替换的占位符"


def test_json_examples_in_templates_stay_parseable():
    """模板中内嵌的 JSON 示例必须仍能被 _extract_json 解析（大括号未被 format 破坏）。"""
    for name in ("PLANNER_SYSTEM", "EVIDENCE_SYSTEM", "ARGUMENT_SYSTEM",
                 "CROSS_EXAM_SYSTEM", "JUDGE_SYSTEM", "CHALLENGER_SYSTEM"):
        tpl = getattr(T, name)
        data = _extract_json(tpl)
        assert isinstance(data, dict), f"{name} 内嵌 JSON 解析失败"


def test_planner_json_schema_keys():
    data = _extract_json(T.PLANNER_SYSTEM)
    assert "proposition" in data and "key_concepts" in data


def test_argument_json_schema_keys():
    data = _extract_json(T.ARGUMENT_SYSTEM)
    assert "claims" in data and "arguments" in data
