"""数据库全量完整性检查；只报告，不静默截断或自动修复。"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

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
    utcnow,
)
from app.services import provider_config, system_settings


def _ids(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item for item in value if isinstance(item, str) and item))


def check_integrity(db: Session) -> dict[str, Any]:
    issues: list[dict[str, str]] = []

    def add(level: str, kind: str, detail: str) -> None:
        issues.append({"level": level, "kind": kind, "detail": detail})

    cases = {row.id: row for row in db.execute(select(Case)).scalars()}
    sources = {row.id: row for row in db.execute(select(Source)).scalars()}
    evidence = {row.id: row for row in db.execute(select(Evidence)).scalars()}
    claims = {row.id: row for row in db.execute(select(Claim)).scalars()}
    arguments = {row.id: row for row in db.execute(select(Argument)).scalars()}
    verdicts = {row.id: row for row in db.execute(select(Verdict)).scalars()}
    cross_exams = {row.id: row for row in db.execute(select(CrossExamination)).scalars()}
    questions = {row.id: row for row in db.execute(select(UserQuestion)).scalars()}

    case_bound = (sources, evidence, claims, arguments, verdicts, cross_exams, questions)
    for rows in case_bound:
        for row in rows.values():
            if row.case_id not in cases:
                add("FAIL", "orphan_case_output", f"{row.__tablename__} {row.id} 引用了不存在的案件 {row.case_id}")

    for item in evidence.values():
        source = sources.get(item.source_id)
        if source is None:
            add("FAIL", "evidence_without_source", f"证据 {item.id}（案件 {item.case_id}）缺少有效来源")
        elif source.case_id != item.case_id:
            add("FAIL", "evidence_source_cross_case", f"证据 {item.id} 与来源 {source.id} 不属于同一案件")

    for item in cross_exams.values():
        if item.target_argument_id:
            argument = arguments.get(item.target_argument_id)
            if argument is None:
                add("FAIL", "cross_exam_missing_argument", f"质证 {item.id} 引用了不存在的论证 {item.target_argument_id}")
            elif argument.case_id != item.case_id:
                add("FAIL", "cross_exam_argument_cross_case", f"质证 {item.id} 与目标论证不属于同一案件")

    link_specs = (
        (Claim, claims, "evidence_ids", ClaimEvidence, "claim_id", evidence, "evidence_id", "claim_evidence"),
        (Argument, arguments, "evidence_ids", ArgumentEvidence, "argument_id", evidence, "evidence_id", "argument_evidence"),
        (Argument, arguments, "claim_ids", ArgumentClaim, "argument_id", claims, "claim_id", "argument_claim"),
        (Verdict, verdicts, "strongest_evidence_ids", VerdictStrongestEvidence, "verdict_id", evidence, "evidence_id", "verdict_strongest"),
        (Verdict, verdicts, "strongest_counter_evidence_ids", VerdictCounterEvidence, "verdict_id", evidence, "evidence_id", "verdict_counter"),
        (CrossExamination, cross_exams, "related_evidence_ids", CrossExamEvidence, "cross_exam_id", evidence, "evidence_id", "cross_exam_evidence"),
        (UserQuestion, questions, "related_evidence_ids", UserQuestionEvidence, "user_question_id", evidence, "evidence_id", "user_question_evidence"),
    )
    for _owner_type, owners, json_attr, link_type, owner_attr, targets, target_attr, kind in link_specs:
        normalized: dict[str, set[str]] = defaultdict(set)
        for link in db.execute(select(link_type)).scalars():
            owner_id = getattr(link, owner_attr)
            target_id = getattr(link, target_attr)
            owner = owners.get(owner_id)
            target = targets.get(target_id)
            if owner is None or target is None:
                add("FAIL", f"{kind}_orphan", f"关系 {owner_id} -> {target_id} 存在缺失端点")
                continue
            if owner.case_id != link.case_id or target.case_id != link.case_id:
                add("FAIL", f"{kind}_cross_case", f"关系 {owner_id} -> {target_id} 的案件归属不一致")
                continue
            normalized[owner_id].add(target_id)
        for owner in owners.values():
            compat = _ids(getattr(owner, json_attr, None))
            for target_id in compat:
                target = targets.get(target_id)
                if target is None:
                    add("FAIL", f"{kind}_missing_target", f"{owner.id}.{json_attr} 引用了不存在的 {target_id}")
                elif target.case_id != owner.case_id:
                    add("FAIL", f"{kind}_json_cross_case", f"{owner.id}.{json_attr} 引用了其他案件的 {target_id}")
            if set(compat) != normalized.get(owner.id, set()):
                add("FAIL", f"{kind}_out_of_sync", f"{owner.id}.{json_attr} 与规范关系表不同步")

    sources_by_case: dict[str, list[Source]] = defaultdict(list)
    for source in sources.values():
        sources_by_case[source.case_id].append(source)
        if not source.is_demo and not (source.url or "").startswith(("http://", "https://")):
            add("WARNING", "source_url_abnormal", f"非演示来源 {source.id} 的 URL 异常")
        if (source.url or "").startswith("demo://") and not source.is_demo:
            add("FAIL", "mock_not_marked", f"演示来源 {source.id} 未标记 is_demo")
        if source.authority_level is not None and not 1 <= source.authority_level <= 5:
            add("FAIL", "source_authority_level", f"来源 {source.id} 的 authority_level 超出 1..5")

    verdict_by_case: dict[str, list[Verdict]] = defaultdict(list)
    for verdict in verdicts.values():
        verdict_by_case[verdict.case_id].append(verdict)
        support = _ids(verdict.strongest_evidence_ids)
        counter = _ids(verdict.strongest_counter_evidence_ids)
        if verdict.conclusion_stance in ("insufficient", "conditional") and (support or counter):
            add(
                "FAIL", "verdict_nondecisive_has_strongest",
                f"判决 {verdict.id} 为 {verdict.conclusion_stance}，但仍引用 strongest 证据",
            )
        overlap = sorted(set(support) & set(counter))
        if overlap:
            add("FAIL", "verdict_evidence_overlap", f"判决 {verdict.id} 的支持与反例证据重叠：{overlap}")
        expected = {
            "prosecution": ("pro", "con"),
            "defense": ("con", "pro"),
        }.get(verdict.conclusion_stance)
        if expected is not None:
            if not support or not counter:
                add(
                    "FAIL", "verdict_decisive_incomplete",
                    f"决定性判决 {verdict.id} 必须同时保留支持与反例 strongest 证据",
                )
            for attr, ids, stance in (
                ("strongest_evidence_ids", support, expected[0]),
                ("strongest_counter_evidence_ids", counter, expected[1]),
            ):
                for evidence_id in ids:
                    item = evidence.get(evidence_id)
                    source = sources.get(item.source_id) if item is not None else None
                    if item is not None and item.stance != stance:
                        add(
                            "FAIL", "verdict_evidence_wrong_direction",
                            f"判决 {verdict.id}.{attr} 的证据 {evidence_id} 方向应为 {stance}，实际为 {item.stance}",
                        )
                    if source is not None and source.is_demo:
                        add(
                            "FAIL", "verdict_evidence_mock",
                            f"判决 {verdict.id}.{attr} 引用了 Mock 证据 {evidence_id}",
                        )
    for case in cases.values():
        owned_verdicts = verdict_by_case.get(case.id, [])
        if case.status == "verdict_ready" and len(owned_verdicts) != 1:
            add("FAIL", "verdict_ready_without_verdict", f"案件 {case.id} 已完成但判决数量为 {len(owned_verdicts)}")
        if case.status != "verdict_ready" and owned_verdicts:
            add("FAIL", "verdict_on_unready_case", f"未完成案件 {case.id} 存在判决")
        if case.is_public and (case.status != "verdict_ready" or len(owned_verdicts) != 1 or not case.public_id):
            add("FAIL", "invalid_public_state", f"公开案件 {case.id} 未完成、缺判决或缺 public_id")
        if not case.is_public and case.public_id:
            add("WARNING", "private_case_public_id", f"私有案件 {case.id} 仍保留 public_id")

        case_sources = sources_by_case.get(case.id, [])
        if case.is_demo or not any(not source.is_demo for source in case_sources):
            for verdict in owned_verdicts:
                if (
                    verdict.conclusion_stance != "insufficient"
                    or verdict.confidence > 0.15
                    or _ids(verdict.strongest_evidence_ids)
                    or _ids(verdict.strongest_counter_evidence_ids)
                ):
                    add("FAIL", "mock_verdict_assertive", f"全 Mock 案件 {case.id} 的判决错误地表达了现实确定性")

    allowed_settings = set(system_settings.DEFAULTS) | {provider_config.CONFIG_KEY}
    for row in db.execute(select(SystemSetting)).scalars():
        if row.key not in allowed_settings:
            add("WARNING", "unknown_setting", f"存在未知设置 {row.key}")
            continue
        if row.key == provider_config.CONFIG_KEY:
            if not isinstance(row.value, dict):
                add("FAIL", "invalid_provider_setting", "provider_config 必须是 JSON 对象")
            elif set(row.value) - {field for field, _secret, _description in provider_config.FIELDS}:
                add("WARNING", "unknown_provider_setting", "provider_config 含未知字段")
            continue
        default = system_settings.DEFAULTS[row.key][0]
        if type(row.value) is not type(default):
            add("FAIL", "invalid_setting_type", f"设置 {row.key} 的类型错误")
        elif row.key in system_settings.INT_RANGES:
            low, high = system_settings.INT_RANGES[row.key]
            if not low <= row.value <= high:
                add("FAIL", "invalid_setting_range", f"设置 {row.key} 不在 {low}..{high} 范围")

    status = "FAIL" if any(item["level"] == "FAIL" for item in issues) else "WARNING" if issues else "PASS"
    return {
        "status": status,
        "checked_at": utcnow().isoformat() + "Z",
        "issues": issues,
        "issue_count": len(issues),
        "checked_counts": {
            "cases": len(cases), "sources": len(sources), "evidence": len(evidence),
            "claims": len(claims), "arguments": len(arguments), "verdicts": len(verdicts),
            "cross_examinations": len(cross_exams), "user_questions": len(questions),
        },
    }
