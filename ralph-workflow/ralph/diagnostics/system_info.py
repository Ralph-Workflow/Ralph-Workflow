"""System information dataclass for diagnostics."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.git.subprocess_runner import GitRunOptions, run_git

if TYPE_CHECKING:
    from collections.abc import Mapping

#: Signature of the git probe :meth:`SystemInfo.gather` uses: ``(argv) ->
#: trimmed stdout or None``. ``None`` means the command failed, timed out
#: or git is absent. The probe is a parameter rather than a hard-wired
#: call so callers — notably the test suite — can gather system
#: information without forking ``git rev-parse``/``branch``/``status``
#: against the surrounding repository, which costs real wall clock that
#: scales with the size of the working tree.
GitProbe = Callable[[list[str]], "str | None"]


def _get_working_directory() -> str | None:
    try:
        return str(Path.cwd())
    except OSError:
        return None


def _get_shell(env: Mapping[str, str]) -> str | None:
    return env.get("SHELL") or env.get("COMSPEC")


def run_git_probe(args: list[str]) -> str | None:
    """Run a git command for diagnostics and return its trimmed stdout.

    This is the production :data:`GitProbe`. It never raises: a non-zero
    exit, a timeout, or a missing git binary all report ``None``.

    Args:
        args: Git arguments, without the leading ``git``.

    Returns:
        Trimmed stdout on success, otherwise ``None``.
    """
    try:
        result = run_git(args, cwd=None, label="git-diagnostics", options=GitRunOptions(timeout=5))
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _is_git_repo(git_probe: GitProbe) -> bool:
    result = git_probe(["rev-parse", "--is-inside-work-tree"])
    return result == "true"


def _count_uncommitted_changes(git_probe: GitProbe) -> int:
    output = git_probe(["status", "--porcelain"])
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
    def gather(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        git_probe: GitProbe = run_git_probe,
    ) -> SystemInfo:
        """Gather system information.

        Args:
            env: Environment mapping used to resolve the login shell.
                Defaults to ``os.environ``.
            git_probe: Seam used for every git query. Defaults to the real
                subprocess runner; inject a stub to gather without forking
                git.

        Returns:
            A populated :class:`SystemInfo`.
        """
        os_name = sys.platform
        arch = platform.machine()
        working_directory = _get_working_directory()
        shell = _get_shell(dict(os.environ) if env is None else dict(env))
        git_version = git_probe(["--version"])
        git_repo = _is_git_repo(git_probe)
        git_branch = None
        uncommitted_changes = None

        if git_repo:
            git_branch = git_probe(["branch", "--show-current"])
            if git_branch is not None:
                uncommitted_changes = _count_uncommitted_changes(git_probe)

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
