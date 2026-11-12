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
