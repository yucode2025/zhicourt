"""Case Planner 测试。"""
from __future__ import annotations

import pytest

from app.agents.planner import _heuristic_plan, _looks_like_fact_query


@pytest.mark.asyncio
async def test_debatable_question_gets_proposition():
    plan = _heuristic_plan("AI 会不会淘汰程序员")  # type: ignore[arg-type]
    assert plan.suitable
    assert plan.proposition
    assert len(plan.search_queries) >= 3


def test_fact_query_rejected():
    plan = _heuristic_plan("日本首都是哪里")  # type: ignore[arg-type]
    assert not plan.suitable


def test_fact_query_detection():
    assert _looks_like_fact_query("珠穆朗玛峰有多高")
    assert not _looks_like_fact_query("AI 是否会淘汰程序员？")
    assert not _looks_like_fact_query("如何看待远程办公")


def test_question_with_request_prefix_stripped():
    plan = _heuristic_plan("请帮我分析一下：年轻人应该提前还房贷吗？")
    assert not plan.proposition.startswith("请帮我分析")
