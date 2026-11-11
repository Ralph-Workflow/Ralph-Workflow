
from __future__ import annotations

import shutil
from typing import Literal, Protocol, runtime_checkable

from .agent_entry import AgentEntry

AgentStatus = Literal["available", "missing_on_path", "no_cmd"]

__all__ = ["AgentEntry", "AgentStatus", "HasListAgents", "check_agent_availability"]


@runtime_checkable
class HasListAgents(Protocol):

    def list_agents(self) -> list[str]: ...

    def get(self, name: str) -> AgentEntry | None: ...


def check_agent_availability(
    registry: HasListAgents,
) -> list[tuple[str, AgentStatus]]:
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
