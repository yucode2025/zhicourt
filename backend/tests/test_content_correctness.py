"""Mock、Evidence 与 Judge 内容正确性回归测试。"""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

from app.agents import debate, evidence_agent, judge, verdict_writer
from app.models import Evidence, Source
from app.services.zhihu.mock_data import MockZhihuProvider, MockWebSearchProvider
from app.services.zhihu.schemas import NormalizedSearchResult


def _source_from_result(item: NormalizedSearchResult) -> Source:
    return Source(
        id="src_demo",
        case_id="case_demo",
        origin=item.origin,
        kind=item.kind,
        title=item.title,
        url=item.url,
        author=item.author,
        summary=item.summary,
        published_at=item.published_at,
        vote_count=item.vote_count,
        comment_count=item.comment_count,
        is_demo=item.is_demo_source,
    )


def _evidence(item_id: str, stance: str, strength: float, claim: str | None = None) -> Evidence:
    source = Source(
        id=f"src_{item_id}", case_id="case_judge", origin="web", title=item_id,
        url=f"https://{item_id}.example/report", author=item_id,
    )
    return Evidence(
        id=item_id,
        case_id="case_judge",
        source_id=source.id,
        source=source,
        claim=claim or item_id,
        stance=stance,
        evidence_type="opinion",
        summary="",
        strength=strength,
    )


@pytest.mark.asyncio
async def test_mock_results_are_explicit_metadata_only_and_urls_have_stable_query_hash():
    provider = MockZhihuProvider()
    first = await provider.search(" 任意  领域命题？ ", limit=6)
    repeated = await provider.search("任意 领域命题？", limit=6)
    different = await provider.search("另一个命题？", limit=6)

    assert [item.url for item in first] == [item.url for item in repeated]
    assert first[0].url != different[0].url
    assert {item.stance_hint for item in first} == {"pro", "con", "neutral"}
    assert all(item.evidence_type_hint for item in first)
    assert all(item.is_demo_source for item in first)
    assert all(item.published_at == "" and item.vote_count == item.comment_count == 0 for item in first)
    assert all("演示" in item.summary for item in first)

    for item in first:
        parsed = urlparse(item.url)
        params = parse_qs(parsed.query)
        assert parsed.scheme == "demo"
        assert len(params["query_hash"][0]) == 16
        assert params["stance_hint"] == [item.stance_hint]
        assert params["evidence_type_hint"] == [item.evidence_type_hint]


@pytest.mark.asyncio
async def test_mock_web_uses_same_metadata_contract():
    results = await MockWebSearchProvider().search("命题", limit=3)
    assert all(item.origin == "web" for item in results)
    assert all(item.is_demo_source for item in results)
    assert all(item.stance_hint and item.evidence_type_hint for item in results)


def test_evidence_uses_demo_hints_instead_of_title_marker_keywords():
    item = NormalizedSearchResult(
        origin="zhihu",
        kind="answer",
        title="【演示数据】标题中的数据报告不应污染类型",
        url="demo://zhihu/1?query_hash=abc&stance_hint=con&evidence_type_hint=assumption",
        summary="这是一个不声称现实事实的演示前提。",
        stance_hint="con",
        evidence_type_hint="assumption",
        is_demo_source=True,
    )
    extracted = evidence_agent.heuristic_extract("命题", [_source_from_result(item)])

    assert extracted[0]["stance"] == "con"
    assert extracted[0]["evidence_type"] == "assumption"
    assert "演示来源" in extracted[0]["limitations"][0]


def test_llm_demo_classification_is_overridden_by_hints_and_marked():
    item = NormalizedSearchResult(
        origin="web",
        title="【演示数据】演示标题",
        url="demo://web/1?query_hash=abc&stance_hint=pro&evidence_type_hint=opinion",
        summary="演示观点",
        stance_hint="pro",
        evidence_type_hint="opinion",
        is_demo_source=True,
    )
    cleaned = evidence_agent._clean_llm_items(
        {
            "evidence": [
                {
                    "source_index": 0,
                    "claim": "演示观点",
                    "stance": "neutral",
                    "evidence_type": "data",
                    "limitations": [],
                }
            ]
        },
        [_source_from_result(item)],
        0,
    )

    assert cleaned[0]["stance"] == "pro"
    assert cleaned[0]["evidence_type"] == "opinion"
    assert "演示来源" in cleaned[0]["limitations"][0]


@pytest.mark.parametrize(
    ("conclusion_stance", "expected_support", "expected_counter"),
    [
        ("prosecution", ["ev_pro"], ["ev_con"]),
        ("defense", ["ev_con"], ["ev_pro"]),
        ("conditional", [], []),
        ("insufficient", [], []),
    ],
)
def test_judge_strongest_strictly_follows_conclusion_stance(
    conclusion_stance: str, expected_support: list[str], expected_counter: list[str]
):
    evidence = [
        _evidence("ev_neutral", "neutral", 0.99),
        _evidence("ev_pro", "pro", 0.8),
        _evidence("ev_con", "con", 0.7),
    ]
    support, counter, _ = judge._select_verdict_evidence(evidence, conclusion_stance)
    assert support == expected_support
    assert counter == expected_counter
    assert not set(support) & set(counter)


def test_judge_removes_semantic_duplicates_but_keeps_negated_counterclaim():
    duplicate_evidence = [
        _evidence("ev_con", "con", 0.8, "初级岗位数量下降"),
        _evidence("ev_pro_duplicate", "pro", 0.9, "初级岗位数量下降。"),
    ]
    support, counter, complete = judge._select_verdict_evidence(duplicate_evidence, "defense")
    assert support == ["ev_con"]
    assert counter == []
    assert complete is False

    negated_evidence = [
        _evidence("ev_con", "con", 0.8, "初级岗位数量下降"),
        _evidence("ev_pro", "pro", 0.75, "初级岗位数量没有下降"),
    ]
    support, counter, complete = judge._select_verdict_evidence(negated_evidence, "defense")
    assert support == ["ev_con"]
    assert counter == ["ev_pro"]
    assert complete is True


def test_judge_same_hostname_results_cannot_satisfy_independent_source_threshold():
    evidence = [
        _evidence("same_pro_1", "pro", 0.9),
        _evidence("same_pro_2", "pro", 0.8),
        _evidence("same_con", "con", 0.7),
    ]
    for index, item in enumerate(evidence):
        item.source.url = f"https://news.example/articles/{index}"
        item.source.author = f"不同作者 {index}"

    result = judge.heuristic_judge("命题", [], [], evidence, [], [], [])
    assert result["conclusion_stance"] == "insufficient"
    assert result["strongest_evidence_ids"] == []
    assert result["strongest_counter_evidence_ids"] == []

    # 同一发布主体转载到不同站点也不能重复计为独立来源。
    for index, item in enumerate(evidence):
        item.source.url = f"https://site-{index}.example/article"
        item.source.author = "同一机构"
    result = judge.heuristic_judge("命题", [], [], evidence, [], [], [])
    assert result["conclusion_stance"] == "insufficient"


def test_heuristic_conditional_and_insufficient_have_no_strongest_ids():
    insufficient = judge.heuristic_judge(
        "命题",
        [],
        [],
        [_evidence("ev_pro", "pro", 0.8), _evidence("ev_con", "con", 0.7)],
        [],
        [],
        [],
    )
    assert insufficient["conclusion_stance"] == "insufficient"
    assert insufficient["strongest_evidence_ids"] == []
    assert insufficient["strongest_counter_evidence_ids"] == []

    conditional = judge.heuristic_judge(
        "命题",
        [],
        [],
        [
            _evidence("ev_pro_1", "pro", 0.8),
            _evidence("ev_pro_2", "pro", 0.6),
            _evidence("ev_con_1", "con", 0.8),
            _evidence("ev_con_2", "con", 0.6),
        ],
        [],
        [],
        [],
    )
    assert conditional["conclusion_stance"] == "conditional"
    assert conditional["strongest_evidence_ids"] == []
    assert conditional["strongest_counter_evidence_ids"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("conclusion_stance", ["prosecution", "defense", "conditional", "insufficient"])
async def test_llm_judge_output_is_sanitized(monkeypatch, conclusion_stance: str):
    evidence = [
        _evidence("ev_neutral", "neutral", 0.99),
        _evidence("ev_pro", "pro", 0.8),
        _evidence("ev_con", "con", 0.7),
    ]

    async def fake_llm_json(*args, **kwargs):
        return {
            "conclusion": "结论",
            "conclusion_stance": conclusion_stance,
            "confidence": 0.6,
            "strongest_evidence_ids": ["ev_neutral", "ev_pro", "ev_pro", "missing"],
            "strongest_counter_evidence_ids": ["ev_neutral", "ev_con", "ev_pro", "ev_con"],
        }

    monkeypatch.setattr(judge, "llm_json", fake_llm_json)
    result, mode = await judge.run_judge("命题", [], [], evidence, [], [], [], mode="llm")

    assert mode == "llm"
    if conclusion_stance == "defense":
        assert result["strongest_evidence_ids"] == ["ev_con"]
        assert result["strongest_counter_evidence_ids"] == ["ev_pro"]
    elif conclusion_stance == "prosecution":
        assert result["strongest_evidence_ids"] == ["ev_pro"]
        assert result["strongest_counter_evidence_ids"] == ["ev_con"]
    else:
        assert result["strongest_evidence_ids"] == []
        assert result["strongest_counter_evidence_ids"] == []
    assert not set(result["strongest_evidence_ids"]) & set(result["strongest_counter_evidence_ids"])


@pytest.mark.asyncio
async def test_llm_judge_rejects_one_sided_conclusion_and_malformed_lists(monkeypatch):
    evidence = [_evidence(f"p{i}", "pro", 0.8) for i in range(3)]

    async def fake(*args, **kwargs):
        return {"conclusion": "命题完全成立", "conclusion_stance": "prosecution", "confidence": "NaN",
                "shared_facts": "假的共同事实", "core_disputes": [{"invented": True}],
                "strongest_evidence_ids": ["p0"], "strongest_counter_evidence_ids": ["p1"]}

    monkeypatch.setattr(judge, "llm_json", fake)
    result, _ = await judge.run_judge("命题", [], [], evidence, [], [], [], mode="llm")
    assert result["conclusion_stance"] == "insufficient"
    assert result["confidence"] <= 0.2
    assert result["shared_facts"] == result["core_disputes"] == []
    assert result["strongest_evidence_ids"] == result["strongest_counter_evidence_ids"] == []


@pytest.mark.asyncio
async def test_writer_cannot_replace_canonical_conclusion(monkeypatch):
    async def fake(*args, **kwargs):
        return {"summary": "反向改判", "prosecution_summary": "正方陈述"}

    monkeypatch.setattr(verdict_writer, "llm_json", fake)
    result, mode = await verdict_writer.run_verdict_writer(
        {"conclusion": "证据不足", "conclusion_stance": "insufficient"}, "正方", "反方", "质证", mode="llm"
    )
    assert mode == "llm"
    assert result["summary"] == "证据不足"


def test_no_pro_evidence_is_not_recast_as_pro_argument():
    claims, args, _ = debate.heuristic_arguments("prosecution", [_evidence("con", "con", 0.9)])
    assert claims == [] and args[0]["evidence_ids"] == [] and args[0]["strength"] == 0


@pytest.mark.asyncio
async def test_missing_direction_skips_llm_advocate(monkeypatch):
    calls = 0

    async def fake(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {"arguments": [{"title": "伪造", "body": "伪造", "evidence_ids": []}]}

    monkeypatch.setattr(debate, "llm_json", fake)
    _claims, args, mode = await debate.run_advocate(
        "prosecution", "命题", [_evidence("con", "con", 0.9)], mode="llm"
    )
    assert calls == 0 and mode == "heuristic"
    assert args[0]["evidence_ids"] == [] and args[0]["strength"] == 0


@pytest.mark.asyncio
async def test_evidence_llm_fields_are_strictly_cleaned():
    source = Source(id="s", case_id="c", origin="web", kind="page", title="标题", url="https://a.example/x", summary="摘要")
    cleaned = evidence_agent._clean_llm_items({"evidence": [{
        "source_index": 0, "claim": "主张", "stance": "pro", "evidence_type": "data",
        "limitations": ["局限"],
    }]}, [source], 0)
    assert cleaned[0]["stance"] == "pro"
    assert cleaned[0]["evidence_type"] == "data"
    assert cleaned[0]["limitations"] == ["局限"]


def test_normalized_public_urls_drop_credentials_and_relative_values():
    unsafe = NormalizedSearchResult(origin="web", title="x", url="https://user:pass@example.com/x")
    relative = NormalizedSearchResult(origin="web", title="x", url="/relative")
    safe = NormalizedSearchResult(origin="web", title="x", url="https://example.com/x?q=1")
    assert unsafe.url == relative.url == ""
    assert safe.url == "https://example.com/x?q=1"
