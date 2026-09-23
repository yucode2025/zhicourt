"""Evidence Engine 评分测试。"""
from __future__ import annotations

from app.models import Evidence, Source
from app.services.evidence_engine import (
    _authority_score,
    evidence_relation,
    independence_via_domain,
    score_evidence,
    score_source,
    WEIGHTS,
)


def _src(**kw) -> Source:
    defaults = dict(
        id="src_x", case_id="c", origin="zhihu", kind="answer", title="t", url="https://zhihu.com/a",
        author="a", summary="很长的内容" * 100, published_at="2025-05-01",
        vote_count=800, comment_count=50, relevance_score=0.8, independence_score=1.0,
    )
    defaults.update(kw)
    return Source(**defaults)


def _ev(src: Source, **kw) -> Evidence:
    defaults = dict(
        id="ev_x", case_id="c", source_id=src.id, claim="claim", stance="pro",
        evidence_type="data", summary="s", relevance_score=0.8, authority_score=0.6,
    )
    defaults.update(kw)
    ev = Evidence(**defaults)
    ev.source = src
    return ev


def test_weights_sum_to_one():
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_high_quality_source_scores_higher():
    good = _src(vote_count=3000)
    bad = _src(vote_count=2, published_at="2015-01-01", relevance_score=0.2)
    assert score_source(good) > score_source(bad)


def test_score_bounded():
    s = score_source(_src())
    assert 0.0 <= s <= 1.0


def test_evidence_corroboration_raises_score():
    src = _src()
    base = score_evidence(_ev(src), src)
    corroborated = score_evidence(_ev(src), src, corroborated_by=2)
    assert corroborated > base


def test_contradiction_lowers_score():
    src = _src()
    base = score_evidence(_ev(src), src)
    contradicted = score_evidence(_ev(src), src, contradicted_by=2)
    assert contradicted < base


def test_fact_beats_opinion_same_source():
    src = _src()
    fact = score_evidence(_ev(src, evidence_type="fact"), src)
    opinion = score_evidence(_ev(src, evidence_type="opinion"), src)
    assert fact > opinion


def test_domain_independence():
    s1 = _src(id="a", url="https://example.com/1")
    s2 = _src(id="b", url="https://example.com/2")
    s3 = _src(id="c", url="https://other.org/x")
    independence_via_domain([s1, s2, s3])
    assert s1.independence_score < 1.0
    assert s3.independence_score == 1.0


def test_authority_uses_hostname_not_path_userinfo_or_suffix():
    assert _authority_score(_src(origin="web", url="https://who.int/report")) == 1.0
    for url in ("https://who.int.attacker.test/x", "https://attacker.test/who.int", "https://who.int@attacker.test/x"):
        assert _authority_score(_src(origin="web", url=url)) == 0.4


def test_corroboration_requires_related_claims_and_distinct_sources():
    a = _ev(_src(id="a"), claim="人工智能减少初级岗位招聘", stance="pro")
    related = _ev(_src(id="b", url="https://other.org/2"), claim="人工智能减少初级岗位数量", stance="pro")
    unrelated = _ev(_src(id="c"), claim="气候变暖导致海平面上升", stance="pro")
    assert evidence_relation(a, related)
    assert not evidence_relation(a, unrelated)
    assert not evidence_relation(a, _ev(_src(id="a"), claim=related.claim))
    same_host = _ev(_src(id="d", url="https://zhihu.com/other"), claim=related.claim, stance="pro")
    assert not evidence_relation(a, same_host)
    demo = _ev(_src(id="e", url="https://third.example/x", is_demo=True), claim=related.claim, stance="pro")
    assert not evidence_relation(a, demo)


def test_official_authority_level_is_used_when_present():
    source = _src(origin="web", url="https://unknown.example/x")
    source.official_authority_level = 5
    assert _authority_score(source) == 1.0


def test_demo_evidence_has_no_strength_even_with_corroboration():
    src = _src(is_demo=True)
    assert score_evidence(_ev(src), src, corroborated_by=10) == 0
