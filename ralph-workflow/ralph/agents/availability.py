"""Agent PATH availability checks for Ralph Workflow.

Shared helper used by both the first-run welcome banner and the
`ralph --diagnose` command to determine whether configured agents
are reachable on the system PATH.
"""

from __future__ import annotations

import shutil
from typing import Literal, Protocol, runtime_checkable

from .agent_entry import AgentEntry

AgentStatus = Literal["available", "missing_on_path", "no_cmd"]

__all__ = ["AgentEntry", "AgentStatus", "HasListAgents", "check_agent_availability"]


@runtime_checkable
class HasListAgents(Protocol):
    """Protocol for agent registries used in availability checks."""

    def list_agents(self) -> list[str]: ...

    def get(self, name: str) -> AgentEntry | None: ...


def check_agent_availability(
    registry: HasListAgents,
) -> list[tuple[str, AgentStatus]]:
    """Check which agents are available on PATH.

    Args:
        registry: Object implementing list_agents() and get(name) for agent resolution.

    Returns:
        List of (registry_name, status) tuples where status is one of
        'available', 'missing_on_path', or 'no_cmd'.
        The key is always the configured registry name so callers can join
        back to the registry without a secondary display-name lookup.
    """
    results: list[tuple[str, AgentStatus]] = []
    for name in registry.list_agents():
        agent = registry.get(name)
        if agent is None:
            continue
        cmd = agent.cmd
        if not cmd:
            results.append((name, "no_cmd"))
            continue
        first_word = cmd.split(maxsplit=1)[0]
        status: AgentStatus = (
            "available" if shutil.which(first_word) is not None else "missing_on_path"
        )
        results.append((name, status))
    return results
