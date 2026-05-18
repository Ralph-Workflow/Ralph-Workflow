"""Agent and system diagnostics.

This module provides comprehensive diagnostic information for troubleshooting
Ralph configuration and environment issues.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.diagnostics.agent_diagnostics import AgentDiagnostics, _is_agent_available
from ralph.diagnostics.agent_status import AgentStatus
from ralph.diagnostics.system_info import SystemInfo

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from ralph.agents.registry import AgentRegistry


@dataclass
class DiagnosticReport:
    """Complete diagnostic report combining system and agent information.

    Attributes:
        system: System information.
        agents: Agent availability diagnostics.
    """

    system: SystemInfo
    agents: AgentDiagnostics


def run_diagnostics(
    registry: AgentRegistry,
    *,
    env: Mapping[str, str] | None = None,
    is_available_fn: Callable[[str], bool] = _is_agent_available,
) -> DiagnosticReport:
    """Run all diagnostics and return the combined report.

    Args:
        registry: Agent registry to check for diagnostics.
        env: Environment mapping for diagnostic commands (defaults to os.environ).
        is_available_fn: Callable to check if an agent command is available.

    Returns:
        DiagnosticReport containing all diagnostic information.
    """
    system = SystemInfo.gather(env=env)
    agents = AgentDiagnostics.test(registry, is_available_fn=is_available_fn)

    return DiagnosticReport(system=system, agents=agents)


__all__ = [
    "AgentDiagnostics",
    "AgentStatus",
    "DiagnosticReport",
    "SystemInfo",
    "run_diagnostics",
]
