"""Defense Agent：基于 Evidence 与反例构建最强反对论证。"""
from __future__ import annotations

from app.agents.debate import run_advocate

DEFENSE = "defense"


async def run_defense(proposition: str, evidence, *, mode: str):
    return await run_advocate(DEFENSE, proposition, evidence, mode=mode)
