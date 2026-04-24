"""Agent PATH availability checks for Ralph Workflow.

Shared helper used by both the first-run welcome banner and the
`ralph --diagnose` command to determine whether configured agents
are reachable on the system PATH.
"""

from __future__ import annotations

import shutil
from typing import Literal, Protocol, runtime_checkable

AgentStatus = Literal["available", "missing_on_path", "no_cmd"]


class _AgentEntry(Protocol):
    """Minimal agent config interface for availability checks."""

    cmd: str
    display_name: str | None


@runtime_checkable
class HasListAgents(Protocol):
    """Protocol for agent registries used in availability checks."""

    def list_agents(self) -> list[str]:
        ...

    def get(self, name: str) -> _AgentEntry | None:
        ...


def check_agent_availability(
    registry: HasListAgents,
) -> list[tuple[str, AgentStatus]]:
    """Check which agents are available on PATH.

    Args:
        registry: Object implementing list_agents() and get(name) for agent resolution.

    Returns:
        List of (display_name, status) tuples where status is one of
        'available', 'missing_on_path', or 'no_cmd'.
    """
    results: list[tuple[str, AgentStatus]] = []
    for name in registry.list_agents():
        agent = registry.get(name)
        if agent is None:
            continue
        cmd = agent.cmd
        if not cmd:
            results.append((agent.display_name or name, "no_cmd"))
            continue
        first_word = cmd.split(maxsplit=1)[0]
        display = agent.display_name or first_word
        status: AgentStatus = (
            "available" if shutil.which(first_word) is not None else "missing_on_path"
        )
        results.append((display, status))
    return results
