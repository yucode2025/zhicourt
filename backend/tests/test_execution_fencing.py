"""执行 fencing：租约认领、stale 拒写、generation 事件与并发安全。"""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.models import AgentRun, Case, CaseRunAttempt, Source, gen_id
from app.services.case_lifecycle import (
    LEASE_TTL_SECONDS,
    claim_lease,
    clear_case_outputs,
    fenced_case,
    lease_owner_id,
    prepare_retry,
    start_generation,
    utcnow,
)
from app.services.execution_mode import derive_engine_mode
from app.core.auth import hash_password
from app.models import User


@pytest.fixture()
def db(db_session):
    return db_session


def _make_case(db, case_id="case_fence", status="queued", **kwargs) -> Case:
    case = Case(
        id=case_id, title="t", original_question="远程办公是否值得长期推广？",
        proposition="远程办公是否值得长期推广", status=status, **kwargs,
    )
    db.add(case)
    db.commit()
    return case


# ---------- claim_lease：条件认领，DB 权威 ----------

def test_claim_lease_on_queued_case(db):
    case = _make_case(db)
    claimed = claim_lease(db, case.id, owner="w1")
    assert claimed is not None
    token, generation, attempt = claimed
    assert generation == 0
    assert attempt == 1
    db.expire_all()
    case = db.get(Case, case.id)
    assert case.status == "running"
    assert case.lease_token == token
    assert case.lease_owner == "w1"
    assert case.lease_expires_at is not None
    assert case.run_started_at is not None
    rows = db.query(CaseRunAttempt).filter_by(case_id=case.id).all()
    assert len(rows) == 1 and rows[0].attempt == 1 and rows[0].status == "running"


def test_second_claim_while_running_is_rejected(db):
    case = _make_case(db)
    claim_lease(db, case.id, owner="w1")
    assert claim_lease(db, case.id, owner="w2") is None


def test_claim_rejected_for_created_status(db):
    case = _make_case(db, status="created")
    assert claim_lease(db, case.id, owner="w1") is None


def test_claim_with_expired_lease_resumes_same_generation(db):
    case = _make_case(db)
    start_generation(db, case)
    db.commit()
    token, generation, _ = claim_lease(db, case.id, owner="w1")
    assert generation == 1
    db.expire_all()
    case = db.get(Case, case.id)
    case.last_completed_stage = "debate"
    case.lease_expires_at = utcnow() - timedelta(seconds=1)
    db.commit()

    claimed = claim_lease(db, case.id, owner="w2")
    assert claimed is not None
    new_token, generation2, attempt2 = claimed
    assert generation2 == 1  # 同代恢复，不递增 generation
    assert new_token != token
    assert attempt2 == 2
    db.expire_all()
    case = db.get(Case, case.id)
    assert case.status == "running"
    assert case.resume_from_stage == "cross_exam"  # debate 的下一阶段
    attempts = db.query(CaseRunAttempt).filter_by(case_id=case.id).order_by(CaseRunAttempt.attempt).all()
    assert [row.status for row in attempts] == ["interrupted", "running"]


def test_expired_lease_claim_requires_expiry(db):
    case = _make_case(db)
    claim_lease(db, case.id, owner="w1")
    db.expire_all()
    case = db.get(Case, case.id)
    case.lease_expires_at = utcnow() + timedelta(seconds=LEASE_TTL_SECONDS)
    db.commit()
    assert claim_lease(db, case.id, owner="w2") is None



def test_prepare_retry_atomically_accepts_only_expired_running_lease(db):
    case = _make_case(db)
    old_token, old_generation, _ = claim_lease(db, case.id, owner="w1")

    with pytest.raises(ValueError, match="仍在正常审理中"):
        prepare_retry(db, case)
    db.expire_all()
    case = db.get(Case, case.id)
    assert case.status == "running" and case.lease_token == old_token

    case.lease_expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    prepare_retry(db, case)
    db.commit()
    db.expire_all()
    case = db.get(Case, case.id)
    assert case.status == "queued"
    assert case.run_generation == old_generation + 1
    assert case.lease_token == ""
    attempts = db.query(CaseRunAttempt).filter_by(case_id=case.id).all()
    assert len(attempts) == 1 and attempts[0].status == "interrupted"

# ---------- fenced_case：验证 + 续约 ----------

def test_fenced_case_verifies_generation_token_and_running(db):
    case = _make_case(db)
    token, generation, _ = claim_lease(db, case.id, owner="w1")

    ok = fenced_case(db, case.id, generation, token)
    assert ok is not None
    assert ok.lease_expires_at > utcnow() + timedelta(seconds=LEASE_TTL_SECONDS - 30)

    assert fenced_case(db, case.id, generation + 1, token) is None
    assert fenced_case(db, case.id, generation, "wrong-token") is None

    db.expire_all()
    case = db.get(Case, case.id)
    case.status = "failed"
    db.commit()
    assert fenced_case(db, case.id, generation, token) is None


# ---------- stale worker：不写任何数据 ----------

@pytest.mark.asyncio
async def test_stale_worker_writes_nothing(db_session, monkeypatch):
    from app.workflows.trial import StaleGeneration, TrialWorkflow

    case = _make_case(db_session)
    token, generation, attempt = claim_lease(db_session, case.id, owner="w1")
    before = (case.status, case.progress_index, case.current_stage)

    wf = TrialWorkflow(case.id, generation=generation, lease_token="stale-token", owner="w2", attempt=attempt)
    wf.db = db_session
    with pytest.raises(StaleGeneration):
        await wf._run_async()

    db_session.expire_all()
    case = db_session.get(Case, case.id)
    assert (case.status, case.progress_index, case.current_stage) == before
    assert case.lease_token == token  # 原持有者租约未被破坏
    assert db_session.query(AgentRun).filter_by(case_id=case.id).count() == 0


@pytest.mark.asyncio
async def test_stale_generation_after_retry_writes_nothing(db_session):
    from app.workflows.trial import StaleGeneration, TrialWorkflow

    case = _make_case(db_session, status="failed")
    prepare_retry(db_session, case)
    db_session.commit()
    old_generation = case.run_generation
    token, generation, attempt = claim_lease(db_session, case.id, owner="w1")
    assert generation == old_generation

    # 模拟上一代 worker 仍用旧 generation 写入
    wf = TrialWorkflow(case.id, generation=generation - 1, lease_token=token, owner="w0", attempt=attempt)
    wf.db = db_session
    with pytest.raises(StaleGeneration):
        await wf._run_async()

    db_session.expire_all()
    case = db_session.get(Case, case.id)
    assert case.status == "running"
    assert db_session.query(AgentRun).filter_by(case_id=case.id).count() == 0


@pytest.mark.asyncio
async def test_planner_result_is_not_written_after_lease_is_lost(db_session, monkeypatch):
    from app.agents import planner
    from app.workflows.trial import StaleGeneration, TrialWorkflow

    case = _make_case(db_session)
    token, generation, attempt = claim_lease(db_session, case.id, owner="w1")

    async def llm_unavailable(_self):
        return False

    async def stale_planner(_question):
        stored = db_session.get(Case, case.id)
        stored.lease_token = "replacement-token"
        stored.lease_owner = "w2"
        db_session.commit()
        return planner.TrialPlan(title="new", proposition="new", suitable=True)

    monkeypatch.setattr(TrialWorkflow, "_llm_available", llm_unavailable)
    monkeypatch.setattr(planner, "run_planner", stale_planner)
    wf = TrialWorkflow(case.id, generation=generation, lease_token=token, owner="w1", attempt=attempt)
    wf.db = db_session

    with pytest.raises(StaleGeneration):
        await wf._run_async()

    db_session.expire_all()
    stored = db_session.get(Case, case.id)
    assert stored.lease_token == "replacement-token"
    assert stored.plan is None and stored.last_completed_stage == ""
    assert db_session.query(AgentRun).filter_by(case_id=case.id).count() == 0




@pytest.mark.asyncio
async def test_direct_answer_is_not_written_after_lease_is_lost(db_session, monkeypatch):
    from types import SimpleNamespace
    from app.agents import planner
    from app.workflows import trial

    case = _make_case(db_session)
    token, generation, attempt = claim_lease(db_session, case.id, owner="w1")

    async def llm_unavailable(_self): return False
    async def planned(_question): return planner.TrialPlan(title="plan", proposition="plan")

    class Provider:
        is_demo = False
        async def direct_answer(self, _prompt):
            stored = db_session.get(Case, case.id)
            stored.lease_token = "replacement-token"
            db_session.commit()
            return SimpleNamespace(answer="stale background")

    monkeypatch.setattr(trial.TrialWorkflow, "_llm_available", llm_unavailable)
    monkeypatch.setattr(planner, "run_planner", planned)
    monkeypatch.setattr(trial.system_settings, "get_flag", lambda *_args: True)
    monkeypatch.setattr(trial, "get_zhihu_provider", lambda *_args: Provider())
    wf = trial.TrialWorkflow(case.id, generation=generation, lease_token=token, owner="w1", attempt=attempt)
    wf.db = db_session
    with pytest.raises(trial.StaleGeneration):
        await wf._run_async()

    db_session.expire_all()
    stored = db_session.get(Case, case.id)
    assert "background" not in (stored.plan or {})
    assert db_session.query(AgentRun).filter_by(case_id=case.id, agent="direct_answer").count() == 0




# ---------- manager：活动键 (case_id, generation) 与条件认领 ----------
def test_recovery_sweeper_runs_recovery_repeatedly(monkeypatch):
    from app.workflows import manager

    calls = []

    class StopAfterOneSweep:
        waits = 0

        def wait(self, _interval):
            self.waits += 1
            return self.waits > 1

    monkeypatch.setattr(manager, "recover_cases", lambda: calls.append(True) or {"recovered": 0, "deferred": 0})
    manager._recovery_sweep_loop(StopAfterOneSweep(), 1)
    assert calls == [True]



def test_submit_case_claims_and_dedups(db, monkeypatch):
    from app.workflows import manager

    case = _make_case(db)
    spawned = []
    monkeypatch.setattr(manager, "_spawn", lambda *args: spawned.append(args) or "submitted")

    assert manager.submit_case(db, case.id) == "submitted"
    assert len(spawned) == 1
    # _spawn(case_id, generation, lease_token, owner, attempt)
    assert spawned[0][0] == case.id
    assert spawned[0][1] == 0  # generation
    assert spawned[0][2]  # lease_token
    assert spawned[0][4] == 1  # attempt

    # 同一案件第二个提交：DB 权威认领失败 → already_active
    assert manager.submit_case(db, case.id) == "already_active"
    assert len(spawned) == 1


def test_submit_case_queue_full_releases_lease(db, monkeypatch):
    from app.workflows import manager

    case = _make_case(db)
    monkeypatch.setattr(manager, "_spawn", lambda *args: "queue_full")
    assert manager.submit_case(db, case.id) == "queue_full"
    db.expire_all()
    case = db.get(Case, case.id)
    assert case.status == "queued"  # 租约归还，等待下次提交或失败兜底
    assert case.lease_token == ""
    attempts = db.query(CaseRunAttempt).filter_by(case_id=case.id).all()
    assert attempts[0].status == "interrupted"


def test_old_generation_active_key_does_not_block_new_generation(db, monkeypatch):
    from app.workflows import manager

    case = _make_case(db)
    # 模拟旧 generation 的活动记录遗留（failed 后未释放的旧实现缺陷）
    manager._active.add((case.id, 0))
    try:
        start_generation(db, case)
        db.commit()
        spawned = []
        monkeypatch.setattr(manager, "_spawn", lambda *args: spawned.append(args) or "submitted")
        assert manager.submit_case(db, case.id) == "submitted"
        assert spawned[0][1] == case.run_generation == 1
    finally:
        manager._active.discard((case.id, 0))


def test_claim_lease_failure_marks_attempt_interrupted_and_next_attempt_increments(db):
    case = _make_case(db)
    claim_lease(db, case.id, owner="w1")
    db.expire_all()
    case = db.get(Case, case.id)
    case.lease_expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    claim_lease(db, case.id, owner="w2")
    rows = db.query(CaseRunAttempt).filter_by(case_id=case.id).order_by(CaseRunAttempt.attempt).all()
    assert [row.attempt for row in rows] == [1, 2]


# ---------- lifecycle：retry 重置 engine_mode / is_demo / publish ----------

def test_prepare_retry_resets_execution_and_publish_state(db):
    from app.models import Verdict

    case = _make_case(db, status="failed")
    case.engine_mode = "mixed"
    case.is_demo = True
    case.is_public = True
    case.public_id = "pub-xyz"
    case.failure_stage = "debate"
    case.failure_kind = "runtime"
    case.failure_code = "Boom"
    case.retry_count = 2
    case.last_completed_stage = "debate"
    db.add(Verdict(id=gen_id("vd"), case_id=case.id, conclusion="x"))
    db.commit()

    prepare_retry(db, case)
    db.commit()
    db.expire_all()
    case = db.get(Case, case.id)
    assert case.status == "queued"
    assert case.engine_mode == "heuristic"
    assert case.is_demo is False
    assert case.is_public is False
    assert case.public_id is None
    assert case.failure_stage == "" and case.failure_kind == "" and case.failure_code == ""
    assert case.last_completed_stage == "" and case.resume_from_stage == ""
    assert case.retry_count == 3
    assert db.query(Verdict).filter_by(case_id=case.id).count() == 0
    assert db.query(Source).filter_by(case_id=case.id).count() == 0


def test_clear_case_outputs_keeps_untouched(db):
    case = _make_case(db)
    clear_case_outputs(db, case)
    assert case.progress_events == [] and case.progress_index == 0


# ---------- engine_mode helper ----------

def test_derive_engine_mode_excludes_verdict_writer_provider_and_failed():
    def run(agent, mode, status="done"):
        return AgentRun(id=gen_id("run"), case_id="c", agent=agent, mode=mode, status=status)

    assert derive_engine_mode([]) is None
    assert derive_engine_mode([run("planner", "llm")]) == "llm"
    assert derive_engine_mode([run("planner", "llm"), run("judge", "heuristic")]) == "mixed"
    assert derive_engine_mode([run("planner", "llm", status="failed")]) is None
    assert derive_engine_mode([run("verdict_writer", "llm")]) is None
    assert derive_engine_mode([run("judge", "provider")]) is None
    assert derive_engine_mode([run("planner", "llm"), run("planner", "mixed")]) == "mixed"
    # reused 与 done 同权重
    assert derive_engine_mode([run("planner", "llm", status="reused")]) == "llm"


# ---------- API：generation-aware events ----------

def _login(client, db, username="fencer") -> str:
    user_id = f"usr_{username}"
    db.add(User(id=user_id, username=username, password_hash=hash_password("password123")))
    db.commit()
    resp = client.post("/api/auth/login", json={"username": username, "password": "password123"})
    assert resp.status_code == 200
    return user_id


def test_events_poll_returns_generation_and_after_minus_one(client, db, monkeypatch):
    # SSE 分支在应用事件循环线程里用 SessionLocal；测试内统一切到测试会话。
    monkeypatch.setattr("app.db.session.SessionLocal", lambda: db)
    user_id = _login(client, db)
    case = _make_case(db, status="running", user_id=user_id)
    case.run_generation = 3
    case.progress_events = [
        {"i": 0, "ts": 1, "stage": "init", "message": "a", "generation": 3},
        {"i": 1, "ts": 2, "stage": "plan", "message": "b", "generation": 3},
        {"i": 2, "ts": 3, "stage": "done", "message": "c", "generation": 3},
    ]
    case.progress_index = 3
    case.status = "verdict_ready"
    db.commit()

    body = client.get(f"/api/cases/{case.id}/events?after=-1").json()
    assert body["generation"] == 3
    assert [e["i"] for e in body["events"]] == [0, 1, 2]
    assert body["status"] == "verdict_ready"

    body = client.get(f"/api/cases/{case.id}/events?after=1").json()
    assert [e["i"] for e in body["events"]] == [2]

    # 无 after 参数 → SSE
    assert client.get(f"/api/cases/{case.id}/events").headers["content-type"].startswith("text/event-stream")


def test_sse_ids_include_generation_and_last_event_id(client, db, monkeypatch):
    monkeypatch.setattr("app.db.session.SessionLocal", lambda: db)
    user_id = _login(client, db)
    case = _make_case(db, status="running", user_id=user_id)
    case.run_generation = 5
    case.progress_events = [
        {"i": 0, "ts": 1, "stage": "init", "message": "a", "generation": 5},
        {"i": 1, "ts": 2, "stage": "plan", "message": "b", "generation": 5},
    ]
    case.progress_index = 2
    case.status = "verdict_ready"
    db.commit()

    with client.stream("GET", f"/api/cases/{case.id}/events") as response:
        body = "".join(chunk for chunk in response.iter_text())
    assert "id: 5:0" in body and "id: 5:1" in body
    assert 'event: done' in body and '"generation": 5' in body

    # Last-Event-ID 断点续传：只推 index > 1 的事件（此处无新事件，直接 done）
    with client.stream("GET", f"/api/cases/{case.id}/events", headers={"last-event-id": "5:1"}) as response:
        body = "".join(chunk for chunk in response.iter_text())
    assert "id: 5:0" not in body and "event: done" in body


# ---------- API：start 递增 generation ----------

def test_start_case_increments_generation(client, db, monkeypatch):
    from app.api import routes_cases

    _login(client, db)
    case = _make_case(db, status="created", user_id="usr_fencer")
    monkeypatch.setattr(routes_cases, "submit_case", lambda *args: "submitted")
    resp = client.post(f"/api/cases/{case.id}/start")
    assert resp.status_code == 200
    db.expire_all()
    stored = db.get(Case, case.id)
    assert stored.run_generation == 1
    assert stored.status == "queued"  # submit_case 被 monkeypatch，认领未发生
    assert db.query(CaseRunAttempt).filter_by(case_id=case.id).count() == 0
