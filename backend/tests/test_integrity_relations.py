"""0004 ORM 约束、兼容 JSON 同步与全量完整性检查。"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from app.models import (
    Argument,
    ArgumentClaim,
    ArgumentEvidence,
    Case,
    Claim,
    ClaimEvidence,
    CrossExamEvidence,
    CrossExamination,
    Evidence,
    Source,
    SystemSetting,
    UserQuestion,
    UserQuestionEvidence,
    Verdict,
    VerdictCounterEvidence,
    VerdictStrongestEvidence,
)
from app.schemas.case import ArgumentOut, SourceOut
from app.services.integrity import check_integrity


def _case_graph():
    case = Case(id="case_rel", title="关系", original_question="question", proposition="proposition")
    source = Source(
        id="src_rel", case_id=case.id, origin="web", title="source",
        url="https://example.com/report", authority_level=4,
    )
    evidence = Evidence(
        id="ev_rel", case_id=case.id, source_id=source.id, claim="evidence",
        stance="pro", evidence_type="fact", strength=0.8,
    )
    claim = Claim(id="clm_rel", case_id=case.id, side="pro", text="claim", evidence_ids=[evidence.id])
    argument = Argument(
        id="arg_rel", case_id=case.id, side="prosecution", title="argument", body="body",
        evidence_ids=[evidence.id], claim_ids=[claim.id], strength=0.8,
    )
    cross = CrossExamination(
        id="cx_rel", case_id=case.id, target_side="prosecution", target_argument_id=argument.id,
        issue_type="data_quality", severity="low", description="cross", related_evidence_ids=[evidence.id],
    )
    question = UserQuestion(
        id="uq_rel", case_id=case.id, target="evidence", text="question",
        challenge_type="evidence", related_evidence_ids=[evidence.id],
    )
    counter_source = Source(
        id="src_rel_counter", case_id=case.id, origin="web", title="counter source",
        url="https://counter.example/report", authority_level=3,
    )
    counter_evidence = Evidence(
        id="ev_rel_counter", case_id=case.id, source_id=counter_source.id, claim="counter evidence",
        stance="con", evidence_type="fact", strength=0.7,
    )
    verdict = Verdict(
        id="vd_rel", case_id=case.id, conclusion="conclusion", conclusion_stance="prosecution",
        confidence=0.7, strongest_evidence_ids=[evidence.id],
        strongest_counter_evidence_ids=[counter_evidence.id],
    )
    return case, source, evidence, claim, argument, cross, question, counter_source, counter_evidence, verdict


def test_sqlite_fk_enabled_and_compat_json_syncs_all_relations(db_session):
    assert db_session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    rows = _case_graph()
    db_session.add_all(rows)
    db_session.commit()

    relation_types = (
        ClaimEvidence, ArgumentEvidence, ArgumentClaim, VerdictStrongestEvidence,
        VerdictCounterEvidence, CrossExamEvidence, UserQuestionEvidence,
    )
    for relation_type in relation_types:
        assert db_session.scalar(select(func.count()).select_from(relation_type)) == 1

    source, argument = rows[1], rows[4]
    db_session.expire_all()
    source = db_session.get(Source, source.id)
    argument = db_session.get(Argument, argument.id)
    assert SourceOut.model_validate(source).authority_level == 4
    assert ArgumentOut.model_validate(argument).claim_ids == ["clm_rel"]

    argument.evidence_ids = []
    argument.claim_ids = []
    db_session.commit()
    assert db_session.scalar(select(func.count()).select_from(ArgumentEvidence)) == 0
    assert db_session.scalar(select(func.count()).select_from(ArgumentClaim)) == 0


def test_json_sync_rejects_missing_and_cross_case_references(db_session):
    case1 = Case(id="case_one", title="one", original_question="q", proposition="p")
    case2 = Case(id="case_two", title="two", original_question="q", proposition="p")
    source = Source(id="src_two", case_id=case2.id, origin="web", title="s", url="https://example.com")
    evidence = Evidence(id="ev_two", case_id=case2.id, source_id=source.id, claim="e")
    db_session.add_all([case1, case2, source, evidence])
    db_session.commit()

    db_session.add(Argument(
        id="arg_bad", case_id=case1.id, side="prosecution", title="bad", body="bad",
        evidence_ids=[evidence.id, "missing"],
    ))
    with pytest.raises(ValueError, match="invalid references"):
        db_session.commit()
    db_session.rollback()


def test_verdict_write_normalizes_nondecisive_and_rejects_invalid_decisive_links(db_session):
    rows = _case_graph()
    verdict = rows[-1]
    verdict.conclusion_stance = "conditional"
    db_session.add_all(rows)
    db_session.commit()
    assert verdict.strongest_evidence_ids == []
    assert verdict.strongest_counter_evidence_ids == []
    assert db_session.scalar(select(func.count()).select_from(VerdictStrongestEvidence)) == 0
    assert db_session.scalar(select(func.count()).select_from(VerdictCounterEvidence)) == 0

    verdict.conclusion_stance = "prosecution"
    verdict.strongest_evidence_ids = ["ev_rel"]
    verdict.strongest_counter_evidence_ids = ["ev_rel"]
    with pytest.raises(ValueError, match="sets overlap"):
        db_session.commit()
    db_session.rollback()

    verdict.conclusion_stance = "prosecution"
    verdict.strongest_evidence_ids = ["ev_rel_counter"]
    verdict.strongest_counter_evidence_ids = ["ev_rel"]
    with pytest.raises(ValueError, match="stance/non-Mock/case invariant"):
        db_session.commit()
    db_session.rollback()


def test_integrity_reports_decisive_wrong_direction_and_mock_evidence(db_session):
    rows = _case_graph()
    db_session.add_all(rows)
    db_session.commit()
    verdict = rows[-1]
    db_session.execute(
        update(Verdict).where(Verdict.id == verdict.id).values(
            strongest_evidence_ids=["ev_rel_counter"],
            strongest_counter_evidence_ids=["ev_rel"],
        )
    )
    db_session.execute(update(Source).where(Source.id == "src_rel_counter").values(is_demo=True))
    db_session.commit()

    kinds = {item["kind"] for item in check_integrity(db_session)["issues"]}
    assert "verdict_evidence_wrong_direction" in kinds
    assert "verdict_evidence_mock" in kinds


def test_composite_fk_rejects_cross_case_source_even_via_core_sql(db_session):
    case1 = Case(id="case_fk1", title="one", original_question="q", proposition="p")
    case2 = Case(id="case_fk2", title="two", original_question="q", proposition="p")
    source = Source(id="src_fk2", case_id=case2.id, origin="web", title="s", url="https://example.com")
    db_session.add_all([case1, case2, source])
    db_session.commit()

    with pytest.raises(IntegrityError):
        db_session.execute(Evidence.__table__.insert().values(
            id="ev_bad_fk", case_id=case1.id, source_id=source.id, claim="bad",
            stance="neutral", evidence_type="opinion", summary="", relevance_score=0,
            authority_score=0, strength=0, limitations=[], created_at=datetime(2026, 9, 11),
        ))
    db_session.rollback()


def test_database_checks_reject_invalid_score(db_session):
    case = Case(id="case_check", title="check", original_question="q", proposition="p")
    db_session.add(case)
    db_session.commit()
    db_session.add(Argument(
        id="arg_check", case_id=case.id, side="prosecution", title="bad", body="bad", strength=-0.1,
    ))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_integrity_checks_full_dataset_mock_public_and_settings(db_session):
    case = Case(
        id="case_mock_bad", title="mock", original_question="q", proposition="p",
        status="verdict_ready", is_demo=True, is_public=True, public_id="public-mock",
    )
    source = Source(id="src_mock_bad", case_id=case.id, origin="zhihu", title="mock", is_demo=True)
    evidence = Evidence(id="ev_mock_bad", case_id=case.id, source_id=source.id, claim="mock")
    verdict = Verdict(
        id="vd_mock_bad", case_id=case.id, conclusion="certain", conclusion_stance="insufficient",
        confidence=0.9,
    )
    arguments = [
        Argument(id=f"arg_full_{index}", case_id=case.id, side="court", title="a", body="b")
        for index in range(501)
    ]
    db_session.add_all([case, source, evidence, verdict, *arguments])
    db_session.add(SystemSetting(key="max_sources_per_case", value="wrong type"))
    db_session.commit()
    db_session.execute(update(Argument).where(Argument.id == "arg_full_500").values(evidence_ids=[evidence.id]))
    db_session.execute(
        update(Verdict).where(Verdict.id == verdict.id).values(
            conclusion_stance="conditional",
            strongest_evidence_ids=[evidence.id],
            strongest_counter_evidence_ids=[evidence.id],
        )
    )
    db_session.commit()

    result = check_integrity(db_session)
    kinds = {item["kind"] for item in result["issues"]}
    assert result["status"] == "FAIL"
    assert result["checked_counts"]["arguments"] == 501
    assert "argument_evidence_out_of_sync" in kinds
    assert "mock_verdict_assertive" in kinds
    assert "verdict_nondecisive_has_strongest" in kinds
    assert "verdict_evidence_overlap" in kinds
    assert "invalid_setting_type" in kinds
