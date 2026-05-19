"""System information dataclass for diagnostics."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.git.subprocess_runner import GitRunOptions, run_git

if TYPE_CHECKING:
    from collections.abc import Mapping


def _get_working_directory() -> str | None:
    try:
        return str(Path.cwd())
    except OSError:
        return None


def _get_shell(env: Mapping[str, str]) -> str | None:
    return env.get("SHELL") or env.get("COMSPEC")


def _run_git_command(args: list[str]) -> str | None:
    try:
        result = run_git(args, cwd=None, label="git-diagnostics", options=GitRunOptions(timeout=5))
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _is_git_repo() -> bool:
    result = _run_git_command(["rev-parse", "--is-inside-work-tree"])
    return result == "true"


def _count_uncommitted_changes() -> int:
    output = _run_git_command(["status", "--porcelain"])
    if output is None:
        return 0
    return len(output.splitlines())


@dataclass
class SystemInfo:
    """System information for diagnostics."""

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
        """Gather system information."""
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
