"""Agent diagnostics dataclass."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ralph.diagnostics.agent_status import AgentStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.registry import AgentRegistry


def _is_agent_available(cmd: str) -> bool:
    if not cmd:
        return False
    command = cmd.split(maxsplit=1)[0]
    return shutil.which(command) is not None


@dataclass
class AgentDiagnostics:
    """Diagnostics for all agents."""

    total_agents: int
    available_agents: int
    unavailable_agents: int
    agent_status: list[AgentStatus] = field(default_factory=list)

    @classmethod
    def test(
        cls,
        registry: AgentRegistry,
        *,
        is_available_fn: Callable[[str], bool] = _is_agent_available,
    ) -> AgentDiagnostics:
        """Test agent availability using the given registry."""
        all_agents = registry.list_agents()

        agent_statuses: list[AgentStatus] = []
        available_count = 0

        for name in sorted(all_agents):
            config = registry.get(name)
            if config is None:
                continue

            available = is_available_fn(config.cmd)
            display = config.display_name or name
            command = config.cmd.split()[0] if config.cmd else ""

            status = AgentStatus(
                name=name,
                display_name=display,
                available=available,
                json_parser=config.json_parser.value,
                command=command,
            )
            agent_statuses.append(status)

            if available:
                available_count += 1

        total = len(all_agents)
        unavailable = total - available_count

        return cls(
            total_agents=total,
            available_agents=available_count,
            unavailable_agents=unavailable,
            agent_status=agent_statuses,
        )
