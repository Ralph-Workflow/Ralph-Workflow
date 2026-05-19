"""Synchronous git helper backed by ProcessManager."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.git.git_run_result import GitRunResult
from ralph.process.manager import SpawnOptions, get_process_manager

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path


@dataclass(frozen=True)
class GitRunOptions:
    """Options for run_git beyond the required args, cwd, and label."""

    phase: str | None = None
    timeout: float | None = None
    env: Mapping[str, str] | None = None
    check: bool = False
    capture_output: bool = True
    text: bool = True


def run_git(
    args: Sequence[str],
    *,
    cwd: Path | None,
    label: str,
    options: GitRunOptions | None = None,
) -> GitRunResult:
    """Spawn a git subprocess through ProcessManager and return the result.

    When ``options.phase`` is provided the process label becomes
    ``phase:<phase>:git:<label>`` so process_phase_scope can terminate it.

    Raises subprocess.TimeoutExpired if timeout is exceeded.
    Raises subprocess.CalledProcessError if options.check is True and returncode != 0.
    """
    effective_options = options or GitRunOptions()
    phase = effective_options.phase
    effective_label = f"phase:{phase}:git:{label}" if phase is not None else label
    cmd = ("git", *args)
    proc = get_process_manager().spawn(
        cmd,
        SpawnOptions(
            cwd=str(cwd) if cwd else None,
            env=dict(effective_options.env) if effective_options.env is not None else None,
            stdout=subprocess.PIPE if effective_options.capture_output else None,
            stderr=subprocess.PIPE if effective_options.capture_output else None,
            label=effective_label,
            text=effective_options.text,
        ),
    )
    try:
        raw_stdout, raw_stderr = proc.communicate(timeout=effective_options.timeout)
    except subprocess.TimeoutExpired:
        raise

    def _str(v: bytes | str | None) -> str:
        if v is None:
            return ""
        return v.decode() if isinstance(v, bytes) else v

    stdout = _str(raw_stdout)
    stderr = _str(raw_stderr)
    returncode = proc.returncode if proc.returncode is not None else 0

    if effective_options.check and returncode != 0:
        raise subprocess.CalledProcessError(returncode, list(cmd), stdout, stderr)

    return GitRunResult(args=cmd, returncode=returncode, stdout=stdout, stderr=stderr)


__all__ = ["GitRunOptions", "run_git"]
