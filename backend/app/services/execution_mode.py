"""单一 engine_mode 派生 helper。

规则（全系统唯一口径）：
- 只统计阶段性 Agent：planner / research / evidence / prosecutor / defense /
  cross_examiner / judge（排除 verdict_writer，判决书润色不代表审理引擎）。
- 只统计 status 为 done / reused 的运行（排除 failed / running / skipped）。
- 排除 mode == "provider"（直答等外部 provider 调用不是审理引擎模式）。
- mixed 或 llm+heuristic 并存 → "mixed"；只有 llm → "llm"；只有 heuristic → "heuristic"；
  无可统计运行 → None（由调用方决定回退，例如沿用 Case.engine_mode）。

工作流在每个成功 AgentRun 落库的同一事务里调用本 helper 更新 Case.engine_mode；
schema 派生（CaseSummary.derive_execution）复用同一 helper，保证口径一致。
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

ENGINE_STAGE_AGENTS = frozenset(
    {"planner", "research", "evidence", "prosecutor", "defense", "cross_examiner", "judge"}
)

# 计入引擎模式的运行状态（reused：跨恢复被复用的同代运行，仍代表真实执行）。
COUNTED_RUN_STATUSES = frozenset({"done", "reused"})


def _counted_modes(runs: Iterable[Any]) -> set[str]:
    modes: set[str] = set()
    for run in runs:
        agent = getattr(run, "agent", None)
        status = getattr(run, "status", None)
        mode = getattr(run, "mode", None)
        if agent not in ENGINE_STAGE_AGENTS:
            continue
        if status not in COUNTED_RUN_STATUSES:
            continue
        if mode in ("llm", "heuristic", "mixed"):
            modes.add(mode)
    return modes


def derive_engine_mode(runs: Iterable[Any]) -> str | None:
    """从 AgentRun 集合派生 engine_mode；无可统计运行时返回 None。"""
    modes = _counted_modes(runs)
    if not modes:
        return None
    if "mixed" in modes or ("llm" in modes and "heuristic" in modes):
        return "mixed"
    return "llm" if "llm" in modes else "heuristic"
