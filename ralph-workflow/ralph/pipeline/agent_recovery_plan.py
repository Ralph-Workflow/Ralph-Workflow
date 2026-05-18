"""Resolved retry plan for a failed agent invocation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentRecoveryPlan:
    """Resolved retry plan for a failed agent invocation."""

    prompt_file: str
    session_id: str | None
    reason: str
