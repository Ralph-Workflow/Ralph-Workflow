from __future__ import annotations

from dataclasses import dataclass

from .agent_activity_kind import AgentActivityKind

__all__ = ["AgentActivityKind", "AgentActivitySignal"]


@dataclass(frozen=True, slots=True)
class AgentActivitySignal:
    kind: AgentActivityKind
    raw: str = ""
    error_message: str | None = None
    is_harness_echo: bool = False
