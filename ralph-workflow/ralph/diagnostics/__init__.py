"""Agent and system diagnostics.

This module provides comprehensive diagnostic information for troubleshooting
Ralph configuration and environment issues.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.diagnostics.agent_diagnostics import AgentDiagnostics, _is_agent_available
from ralph.diagnostics.agent_status import AgentStatus
from ralph.diagnostics.fs_health import FsHealth
from ralph.diagnostics.system_info import GitProbe, SystemInfo, run_git_probe

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from ralph.agents.registry import AgentRegistry


@dataclass
class DiagnosticReport:
    """Complete diagnostic report combining system and agent information.

    Attributes:
        system: System information.
        agents: Agent availability diagnostics.
        fs_health: Filesystem-environment health for the workspace
            volume (RFC-013 P4). ``None`` when ``workspace_root`` was
            not provided to ``run_diagnostics``.
    """

    system: SystemInfo
    agents: AgentDiagnostics
    fs_health: FsHealth | None = None


def run_diagnostics(
    registry: AgentRegistry,
    *,
    env: Mapping[str, str] | None = None,
    is_available_fn: Callable[[str], bool] = _is_agent_available,
    workspace_root: Path | None = None,
    git_probe: GitProbe = run_git_probe,
) -> DiagnosticReport:
    """Run all diagnostics and return the combined report.

    Args:
        registry: Agent registry to check for diagnostics.
        env: Environment mapping for diagnostic commands (defaults to os.environ).
        is_available_fn: Callable to check if an agent command is available.
        workspace_root: Optional workspace root. When supplied, the
            report also includes an ``FsHealth`` snapshot for the volume
            containing the workspace (Spotlight status, ``.fseventsd``
            journal size, operator warnings).
        git_probe: Seam forwarded to :meth:`SystemInfo.gather` for every
            git query. Defaults to the real subprocess runner.

    Returns:
        DiagnosticReport containing all diagnostic information.
    """
    system = SystemInfo.gather(env=env, git_probe=git_probe)
    agents = AgentDiagnostics.test(registry, is_available_fn=is_available_fn)
    fs_health = FsHealth.gather(workspace_root) if workspace_root is not None else None

    return DiagnosticReport(system=system, agents=agents, fs_health=fs_health)


__all__ = [
    "AgentDiagnostics",
    "AgentStatus",
    "DiagnosticReport",
    "FsHealth",
    "GitProbe",
    "SystemInfo",
    "run_diagnostics",
    "run_git_probe",
]
