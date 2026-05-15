"""Agent and system diagnostics.

Ported from ralph-workflow/src/diagnostics/.

This module provides comprehensive diagnostic information for troubleshooting
Ralph configuration and environment issues.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.git.subprocess_runner import run_git

if TYPE_CHECKING:
        from collections.abc import Callable, Mapping

        from ralph.agents.registry import AgentRegistry


@dataclass
class SystemInfo:
    """System information for diagnostics.

    Attributes:
        os: Operating system name.
        arch: System architecture.
        working_directory: Current working directory.
        shell: Current shell (from SHELL on Unix, ComSpec on Windows).
        git_version: Git version string.
        git_repo: Whether current directory is a git repository.
        git_branch: Current git branch name.
        uncommitted_changes: Number of uncommitted changes.
    """

    os: str
    arch: str
    working_directory: str | None
    shell: str | None
    git_version: str | None
    git_repo: bool
    git_branch: str | None
    uncommitted_changes: int | None

    @classmethod
    def gather(cls, env: Mapping[str, str] | None = None) -> SystemInfo:
        """Gather system information.

        Returns:
            SystemInfo with current system details.
        """
        os_name = sys.platform
        arch = platform.machine()
        working_directory = _get_working_directory()
        shell = _get_shell(dict(os.environ) if env is None else dict(env))
        git_version = _run_git_command(["--version"])
        git_repo = _is_git_repo()
        git_branch = None
        uncommitted_changes = None

        if git_repo:
            git_branch = _run_git_command(["branch", "--show-current"])
            if git_branch is not None:
                uncommitted_changes = _count_uncommitted_changes()

        return cls(
            os=os_name,
            arch=arch,
            working_directory=working_directory,
            shell=shell,
            git_version=git_version,
            git_repo=git_repo,
            git_branch=git_branch,
            uncommitted_changes=uncommitted_changes,
        )


def _get_working_directory() -> str | None:
    """Get current working directory."""
    try:
        return str(Path.cwd())
    except OSError:
        return None


def _get_shell(env: Mapping[str, str]) -> str | None:
    """Get current shell."""
    return env.get("SHELL") or env.get("COMSPEC")


def _run_git_command(args: list[str]) -> str | None:
    """Run a git command and return the output.

    Args:
        args: Git command arguments.

    Returns:
        Command output stripped of whitespace, or None if command failed.
    """
    try:
        result = run_git(args, cwd=None, label="git-diagnostics", timeout=5, check=False)
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _is_git_repo() -> bool:
    """Check if current directory is inside a git repository."""
    result = _run_git_command(["rev-parse", "--is-inside-work-tree"])
    return result == "true"


def _count_uncommitted_changes() -> int:
    """Count uncommitted changes in the git repository."""
    output = _run_git_command(["status", "--porcelain"])
    if output is None:
        return 0
    return len(output.splitlines())


@dataclass
class AgentStatus:
    """Status of a single agent.

    Attributes:
        name: Agent name.
        display_name: Human-readable display name.
        available: Whether the agent is available.
        json_parser: JSON parser type used by the agent.
        command: Command executable name.
    """

    name: str
    display_name: str
    available: bool
    json_parser: str
    command: str

def _is_agent_available(cmd: str) -> bool:
    """Check if an agent command is available.

    Args:
        cmd: Command to check.

    Returns:
        True if the command exists and is executable.
    """
    if not cmd:
        return False
    command = cmd.split(maxsplit=1)[0]
    return shutil.which(command) is not None


@dataclass
class AgentDiagnostics:
    """Diagnostics for all agents.

    Attributes:
        total_agents: Total number of configured agents.
        available_agents: Number of agents that are available.
        unavailable_agents: Number of agents that are unavailable.
        agent_status: List of individual agent statuses.
    """

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
        """Test agent availability using the given registry.

        Args:
            registry: Agent registry to test.
            is_available_fn: Callable to check if an agent command is available.

        Returns:
            AgentDiagnostics with availability information.
        """
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

    This function gathers all diagnostic information without printing.
    The CLI handler is responsible for formatting and displaying results.

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
