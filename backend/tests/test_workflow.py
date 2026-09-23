"""Workflow 全流程测试（Mock Provider + 启发式引擎，SQLite）。"""
from __future__ import annotations

import pytest

from app.workflows.trial import TrialWorkflow, _dedupe
from app.services.zhihu.schemas import NormalizedSearchResult


@pytest.mark.asyncio
async def test_full_workflow_produces_verdict(db_session, monkeypatch):
    from app.models import Case
    from app.services.case_lifecycle import claim_lease

    case = Case(id="case_wf1", title="t", original_question="AI 会不会淘汰程序员？", proposition="AI 是否会显著减少初级岗位需求", status="queued")
    db_session.add(case)
    db_session.commit()
    claimed = claim_lease(db_session, case.id, owner="test")
    assert claimed is not None
    lease_token, generation, attempt = claimed
    direct_answer_calls = 0

    async def forbidden_direct_answer(*args, **kwargs):
        nonlocal direct_answer_calls
        direct_answer_calls += 1
        raise AssertionError("disabled direct answer must not be called")

    from app.services.zhihu.mock_data import MockZhihuProvider
    monkeypatch.setattr(MockZhihuProvider, "direct_answer", forbidden_direct_answer)

    wf = TrialWorkflow("case_wf1", generation=generation, lease_token=lease_token, owner="test", attempt=attempt)
    # 替换 db 会话为测试会话
    wf.db = db_session
    await wf._run_async()

    db_session.expire_all()
    case = db_session.get(Case, "case_wf1")
    assert case.status == "verdict_ready"
    assert case.lease_token == ""  # 最终提交释放 lease
    assert case.last_completed_stage == "verdict"
    assert all(e.get("generation") == generation for e in case.progress_events)
    assert case.plan is not None
    assert len(case.sources) >= 3
    assert len(case.evidence) >= 3
    # 证据可追溯：每条证据都必须有 source
    for ev in case.evidence:
        assert ev.source_id
        assert any(s.id == ev.source_id for s in case.sources)
    assert len(case.arguments) >= 2
    assert case.verdict is not None
    assert case.verdict.conclusion_stance == "insufficient"
    assert case.verdict.confidence <= 0.15
    assert case.verdict.strongest_evidence_ids == []
    assert all(s.is_demo for s in case.sources)
    assert all(e.strength == 0 for e in case.evidence)
    assert case.engine_mode == "heuristic"
    from app.schemas.case import CaseDetail

    detail = CaseDetail.model_validate(case)
    assert detail.source_mode == "mock"
    assert detail.execution_summary["real_sources"] == 0
    assert detail.execution_summary["demo_sources"] == len(case.sources)
    assert direct_answer_calls == 0
    assert case.progress_index > 5


@pytest.mark.asyncio
async def test_workflow_rejects_fact_query(db_session):
    from app.models import Case
    from app.services.case_lifecycle import claim_lease

    case = Case(id="case_wf2", title="t", original_question="日本首都是哪里", proposition="日本首都是哪里", status="queued")
    db_session.add(case)
    db_session.commit()
    claimed = claim_lease(db_session, case.id, owner="test")
    assert claimed is not None
    lease_token, generation, attempt = claimed

    wf = TrialWorkflow("case_wf2", generation=generation, lease_token=lease_token, owner="test", attempt=attempt)
    wf.db = db_session
    await wf._run_async()

    db_session.expire_all()
    case = db_session.get(Case, "case_wf2")
    assert case.status == "failed"
    assert "事实查询" in (case.error_message or "")
    assert case.failure_stage == "plan"
    assert case.failure_code == "not_suitable"


def test_execution_summary_derives_mixed_mode_without_model_change(db_session):
    from app.models import AgentRun, Case, Source
    from app.schemas.case import CaseDetail

    case = Case(id="case_modes", title="混合", original_question="混合", proposition="混合")
    db_session.add_all([case, AgentRun(id="run_1", case_id=case.id, agent="planner", mode="llm", status="done"),
                        AgentRun(id="run_2", case_id=case.id, agent="judge", mode="heuristic", status="done"),
                        Source(id="src_real", case_id=case.id, origin="web", title="真实", is_demo=False),
                        Source(id="src_mock", case_id=case.id, origin="zhihu", title="演示", is_demo=True)])
    db_session.commit()
    detail = CaseDetail.model_validate(case)
    assert detail.engine_mode == detail.source_mode == "mixed"
    assert detail.execution_summary["engine_resolved"] is True
    assert detail.execution_summary["real_sources"] == detail.execution_summary["demo_sources"] == 1


def test_execution_summary_marks_unstarted_case_mode_as_unresolved(db_session):
    from app.models import Case
    from app.schemas.case import CaseSummary

    case = Case(
        id="case_mode_pending", title="等待开庭", original_question="等待开庭",
        proposition="等待开庭", status="queued",
    )
    db_session.add(case)
    db_session.commit()

    summary = CaseSummary.model_validate(case)
    assert summary.engine_mode == "heuristic"  # 兼容数据库历史默认值
    assert summary.execution_summary["engine_resolved"] is False


def test_workflow_persists_sanitized_llm_failure(db_session, monkeypatch):
    from app.agents import planner
    from app.models import Case
    from app.services.case_lifecycle import claim_lease
    from app.services.llm import LLMUnavailable

    case = Case(
        id="case_llm_failure", title="t", original_question="AI 是否可靠？",
        proposition="AI 是否可靠", status="queued",
    )
    db_session.add(case)
    db_session.commit()
    token, generation, attempt = claim_lease(db_session, case.id, owner="test")

    async def llm_available(_self):
        return True

    async def fail(_question):
        raise LLMUnavailable("upstream_unavailable", retryable=True, disposition="transient")

    monkeypatch.setattr(TrialWorkflow, "_llm_available", llm_available)
    monkeypatch.setattr(planner, "run_planner", fail)
    workflow = TrialWorkflow(
        case.id, generation=generation, lease_token=token, owner="test", attempt=attempt,
    )
    workflow.db = db_session
    workflow.run()

    db_session.expire_all()
    stored = db_session.get(Case, case.id)
    assert stored.status == "failed"
    assert stored.failure_kind == "llm_upstream"
    assert stored.failure_code == "upstream_unavailable"
    assert stored.error_message == "模型服务暂时不可用，请稍后重试。"




def test_dedupe_removes_duplicates():
    items = [
        NormalizedSearchResult(origin="zhihu", title="Same Title", url="https://a/1"),
        NormalizedSearchResult(origin="zhihu", title="Same Title", url="https://a/2"),
        NormalizedSearchResult(origin="web", title="Another", url=""),
    ]
    out = _dedupe(items)
    assert len(out) == 2
