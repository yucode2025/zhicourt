"""Evidence Agent 分批 LLM 精读 / mixed / 全降级回归测试。"""
from __future__ import annotations

import pytest

from app.agents import evidence_agent
from app.models import Source


def _sources(n: int = 12) -> list[Source]:
    return [
        Source(
            id=f"src_{i}", case_id="case_x", origin="zhihu", kind="answer",
            title=f"来源 {i}", summary=f"这是第 {i} 条真实来源摘要，包含报告数据和案例。",
            url=f"https://www.zhihu.com/answer/{i}",
        )
        for i in range(n)
    ]


def _llm_items(batch: list[Source], start: int) -> list[dict]:
    return [
        {
            "source_index": start + i,
            "claim": f"LLM 精读 {source.title}",
            "stance": "neutral",
            "evidence_type": "data",
            "summary": source.summary,
            "limitations": [],
        }
        for i, source in enumerate(batch)
    ]


@pytest.mark.asyncio
async def test_all_batches_llm(monkeypatch):
    async def fake_batch(proposition, batch, start):
        return _llm_items(batch, start)

    monkeypatch.setattr(evidence_agent, "_llm_extract_batch", fake_batch)
    items, mode = await evidence_agent.run_evidence_agent("命题", _sources(), mode="llm")
    assert mode == "llm"
    assert len(items) == 12
    assert all("LLM 精读" in item["claim"] for item in items)


@pytest.mark.asyncio
async def test_partial_batch_failure_is_mixed(monkeypatch):
    async def fake_batch(proposition, batch, start):
        if start == 0:
            return _llm_items(batch, start)
        raise TimeoutError("gateway timeout")

    monkeypatch.setattr(evidence_agent, "_llm_extract_batch", fake_batch)
    items, mode = await evidence_agent.run_evidence_agent("命题", _sources(), mode="llm")
    assert mode == "mixed"
    assert len(items) == 12
    assert sum("LLM 精读" in item["claim"] for item in items) == 6
    fallback = [item for item in items if "LLM 精读" not in item["claim"]]
    assert all("来源真实" in item["limitations"][0] for item in fallback)


@pytest.mark.asyncio
async def test_all_batches_failure_is_heuristic(monkeypatch):
    async def fake_batch(proposition, batch, start):
        return []

    monkeypatch.setattr(evidence_agent, "_llm_extract_batch", fake_batch)
    items, mode = await evidence_agent.run_evidence_agent("命题", _sources(), mode="llm")
    assert mode == "heuristic"
    assert len(items) == 12
    assert all("来源真实" in item["limitations"][0] for item in items)


def test_build_rows_tolerates_invalid_llm_shapes():
    sources = _sources(1)
    rows = evidence_agent.build_evidence_rows(
        "case_x",
        sources,
        [
            {"source_index": 0, "claim": "有效", "stance": "bad", "evidence_type": "weird", "limitations": "not-list"},
            {"source_index": "0", "claim": "无效"},
            None,
        ],
    )
    assert len(rows) == 1
    assert rows[0].stance == "neutral"
    assert rows[0].evidence_type == "opinion"
    assert rows[0].limitations == []
