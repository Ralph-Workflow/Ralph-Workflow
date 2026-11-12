from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_activity_kind import AgentActivityKind
else:
    AgentActivityKind = import_module("ralph.agents.agent_activity_kind").AgentActivityKind

__all__ = ["AgentActivityKind", "AgentActivitySignal"]


@dataclass(frozen=True, slots=True)
class AgentActivitySignal:
    kind: AgentActivityKind
    raw: str = ""
    error_message: str | None = None
    is_harness_echo: bool = False
