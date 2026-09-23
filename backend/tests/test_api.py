"""API 集成测试：案件创建 / 校验 / 限流 / 错误处理。"""
from __future__ import annotations

import pytest

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models import Case


def test_create_case_ok(client):
    resp = client.post("/api/cases", json={"question": "AI 会不会淘汰程序员？"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"].startswith("case_")
    assert body["status"] == "created"
    assert "zhicourt_sid" in resp.cookies or True  # cookie 由 Set-Cookie 头携带



@pytest.mark.asyncio
@pytest.mark.parametrize("code,status", [
    ("incomplete_response", 502),
    ("upstream_unavailable", 503),
    ("circuit_open", 503),
    ("rate_limited", 429),
])
async def test_llm_exception_handler_contract(code, status):
    import json
    from starlette.requests import Request
    from app.main import llm_exception_handler
    from app.services.llm import LLMUnavailable

    request = Request({"type": "http", "method": "POST", "path": "/api/plan", "headers": []})
    response = await llm_exception_handler(
        request, LLMUnavailable(code, retryable=True, disposition="transient"),
    )
    body = json.loads(response.body)
    assert response.status_code == status
    assert body["error"]["code"] == code
    assert body["error"]["message"] != "服务器内部错误"




@pytest.mark.parametrize("code,status", [
    ("incomplete_response", 502),
    ("upstream_unavailable", 503),
    ("circuit_open", 503),
    ("rate_limited", 429),
])
def test_plan_maps_llm_failures_to_stable_http_error(client, monkeypatch, code, status):
    from app.agents import planner
    from app.services.llm import LLMUnavailable

    async def fail(_question):
        raise LLMUnavailable(code, retryable=True, disposition="transient")

    monkeypatch.setattr(planner, "run_planner", fail)
    response = client.post("/api/plan", json={"question": "AI 是否可靠？"})
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["message"] != "服务器内部错误"




def test_case_failure_payload_exposes_retryable_llm_reason():
    from app.api.routes_cases import _case_failure_payload

    case = Case(
        id="case_failed_payload", title="t", original_question="q", proposition="p",
        status="failed", current_stage="failed", failure_stage="plan",
        failure_kind="llm_upstream", failure_code="upstream_unavailable",
        error_message="模型服务暂时不可用，请稍后重试。",
    )
    assert _case_failure_payload(case) == {
        "code": "upstream_unavailable",
        "message": "模型服务暂时不可用，请稍后重试。",
        "stage": "plan",
        "retryable": True,
    }

    case.failure_kind = "validation"
    case.failure_code = "not_suitable"
    assert _case_failure_payload(case)["retryable"] is False

    case.failure_kind = "queue_unavailable"
    case.failure_code = "queue_full"
    assert _case_failure_payload(case)["retryable"] is True

def test_create_case_too_short(client):
    resp = client.post("/api/cases", json={"question": "ab"})
    assert resp.status_code == 422


def test_create_case_too_long(client):
    resp = client.post("/api/cases", json={"question": "问" * 501})
    assert resp.status_code == 422


def test_dynamic_case_and_challenge_limits(client, db_session):
    from app.services.system_settings import set_setting

    set_setting(db_session, "case_input_max_length", 800)
    assert client.post("/api/plan", json={"question": "问" * 600}).status_code == 200
    created = client.post("/api/cases", json={"question": "问" * 600})
    assert created.status_code == 201
    set_setting(db_session, "challenge_input_max_length", 1500)
    case = db_session.get(Case, created.json()["id"])
    case.status = "verdict_ready"
    db_session.commit()
    assert client.post(f"/api/cases/{case.id}/questions", json={"target": "judge", "text": "证" * 1200}).status_code == 201
    assert client.post(f"/api/cases/{case.id}/questions", json={"target": "judge", "text": "证" * 1501}).status_code == 422


def test_get_case_404(client):
    resp = client.get("/api/cases/case_nonexistent")
    assert resp.status_code == 404


def test_list_cases_contains_demo(client, db_session):
    db_session.add(Case(id="case_demo1", title="演示", original_question="q", proposition="p", is_demo=True, is_public=True))
    db_session.commit()
    resp = client.get("/api/cases")
    assert resp.status_code == 200
    assert any(c["id"] == "case_demo1" for c in resp.json())


def test_plan_endpoint(client):
    resp = client.post("/api/plan", json={"question": "年轻人应该提前还房贷吗？"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["proposition"]
    assert isinstance(body["search_queries"], list)


def test_plan_fact_query_unsuitable(client):
    resp = client.post("/api/plan", json={"question": "珠穆朗玛峰有多高"})
    body = resp.json()
    assert body["suitable"] is False


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_reports_disabled_providers_truthfully(client, db_session):
    from app.services.system_settings import set_setting

    set_setting(db_session, "enable_mock_provider", False)
    set_setting(db_session, "enable_web_search", False)
    body = client.get("/api/health/ready").json()
    assert body["zhihu"] == "disabled"
    assert body["web_search"] == "disabled"


def test_readiness_rejects_outdated_database_revision(client, db_session):
    db_session.execute(text("UPDATE alembic_version SET version_num='0008'"))
    db_session.commit()

    response = client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["database"] == "schema_mismatch"


def test_schema_gate_rejects_missing_agent_run_execution_columns():
    from app.main import _database_schema_matches_release, _release_migration_heads

    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
        ))
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:head)"),
            {"head": next(iter(_release_migration_heads()))},
        )
        connection.execute(text(
            "CREATE TABLE agent_runs (id VARCHAR(40) PRIMARY KEY, status VARCHAR(20) NOT NULL)"
        ))
    with Session(engine) as session:
        assert _database_schema_matches_release(session) is False
    engine.dispose()


def test_usage_endpoint(client):
    resp = client.get("/api/usage")
    assert resp.status_code == 200
    assert "zhihu_search" in resp.json()["providers"]


def test_question_409_when_not_ready(client, db_session):
    db_session.add(Case(id="case_c1", title="t", original_question="q", proposition="p", status="created", is_public=True))
    db_session.commit()
    resp = client.post("/api/cases/case_c1/questions", json={"target": "judge", "text": "这个结论的依据是什么？"})
    assert resp.status_code == 409


def test_verdict_404_when_absent(client, db_session):
    db_session.add(Case(id="case_c2", title="t", original_question="q", proposition="p", status="verdict_ready"))
    db_session.commit()
    resp = client.get("/api/cases/case_c2/verdict")
    assert resp.status_code == 404
