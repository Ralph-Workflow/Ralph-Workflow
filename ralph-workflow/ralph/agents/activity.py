"""Watchdog-relevant activity signals emitted by agent transports."""

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
    """Small transport-neutral signal consumed by timeout control flow.

    ``error_message`` lets one line feed BOTH repetition dimensions. A failed
    tool call is genuinely both things at once -- a tool call and an error --
    and each dimension catches a wedge the other cannot: the tool dimension
    collapses a repeated call whose failure TEXT varies (a failing test run,
    where the pytest counts differ each attempt), while the error dimension
    collapses a repeated failure whose ARGS vary (an
    ``MCP error -32001: Request timed out`` storm across different tools, the
    exact incident the repeated-error breaker was written for). Forcing the
    classifier to pick one kind meant whichever it picked, the other wedge was
    invisible. Set it alongside ``kind=TOOL_USE`` to feed both.
    """

    kind: AgentActivityKind
    raw: str = ""
    error_message: str | None = None
    is_harness_echo: bool = False
