"""Prosecutor Agent：基于已有 Evidence 构建最强支持论证（禁止创造证据）。"""
from __future__ import annotations

from app.agents.debate import run_advocate

PROSECUTION = "prosecution"


async def run_prosecutor(proposition: str, evidence, *, mode: str):
    return await run_advocate(PROSECUTION, proposition, evidence, mode=mode)
