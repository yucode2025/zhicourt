from __future__ import annotations

from app.core.auth import hash_password
from app.models import Case, Evidence, Source, User, Verdict, VerdictStrongestEvidence, gen_id
from app.services.case_lifecycle import prepare_retry


def test_prepare_retry_clears_partial_outputs(db_session):
    user = User(id="usr_retry", username="retry", password_hash=hash_password("password123"))
    case = Case(id="case_retry", user_id=user.id, title="retry", original_question="q", proposition="p", status="failed")
    source = Source(id="src_retry", case_id=case.id, origin="demo", title="s")
    evidence = Evidence(id="ev_retry", case_id=case.id, source_id=source.id, claim="c")
    verdict = Verdict(id="vd_retry", case_id=case.id, conclusion="old", strongest_evidence_ids=[evidence.id])
    db_session.add_all([user, case, source, evidence, verdict])
    db_session.commit()

    prepare_retry(db_session, case)
    db_session.commit()
    assert case.status == "queued"
    assert db_session.query(Source).filter_by(case_id=case.id).count() == 0
    assert db_session.query(Evidence).filter_by(case_id=case.id).count() == 0
    assert db_session.query(Verdict).filter_by(case_id=case.id).count() == 0
    assert db_session.query(VerdictStrongestEvidence).filter_by(case_id=case.id).count() == 0


def test_retry_endpoint_requires_failed_and_preserves_single_case(client, db_session, monkeypatch):
    user = User(id="usr_retry_api", username="retryapi", password_hash=hash_password("password123"))
    case = Case(id="case_retry_api", user_id=user.id, title="retry", original_question="q", proposition="p", status="failed")
    db_session.add_all([user, case])
    db_session.commit()
    assert client.post("/api/auth/login", json={"username": "retryapi", "password": "password123"}).status_code == 200
    monkeypatch.setattr("app.api.routes_cases.submit_case", lambda *args: "submitted")
    response = client.post("/api/cases/case_retry_api/retry")
    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert db_session.query(Case).filter_by(id=case.id).count() == 1


def test_start_submission_failure_does_not_leave_queued(client, db_session, monkeypatch):
    user = User(id="usr_submit_fail", username="submitfail", password_hash=hash_password("password123"))
    case = Case(
        id="case_submit_fail", user_id=user.id, title="submit",
        original_question="远程办公是否值得长期推广？", proposition="远程办公是否值得长期推广", status="created",
    )
    db_session.add_all([user, case])
    db_session.commit()
    assert client.post("/api/auth/login", json={"username": "submitfail", "password": "password123"}).status_code == 200
    monkeypatch.setattr("app.api.routes_cases.submit_case", lambda *args: "submit_failed")
    response = client.post("/api/cases/case_submit_fail/start")
    assert response.status_code == 503
    db_session.expire_all()
    stored = db_session.get(Case, case.id)
    assert stored.status == "failed"
    assert stored.current_stage == "queue"
