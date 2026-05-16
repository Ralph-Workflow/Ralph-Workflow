"""Synchronous git helper backed by ProcessManager."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.process.manager import get_process_manager

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path


@dataclass(frozen=True)
class GitRunResult:
    """Result of a git subprocess invocation."""

    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


def run_git(
    args: Sequence[str],
    *,
    cwd: Path | None,
    label: str,
    phase: str | None = None,
    timeout: float | None = None,
    env: Mapping[str, str] | None = None,
    check: bool = False,
    capture_output: bool = True,
    text: bool = True,
) -> GitRunResult:
    """Spawn a git subprocess through ProcessManager and return the result.

    When ``phase`` is provided, the process is labeled ``phase:<phase>:git:<label>``
    so that :func:`~ralph.process.manager.process_phase_scope` can terminate it
    when the phase completes.

    Raises subprocess.TimeoutExpired if timeout is exceeded.
    Raises subprocess.CalledProcessError if check=True and returncode != 0.
    """
    effective_label = f"phase:{phase}:git:{label}" if phase is not None else label
    cmd = ("git", *args)
    proc = get_process_manager().spawn(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=dict(env) if env is not None else None,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        label=effective_label,
        text=text,
    )
    try:
        raw_stdout, raw_stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        raise

    def _str(v: bytes | str | None) -> str:
        if v is None:
            return ""
        return v.decode() if isinstance(v, bytes) else v

    stdout = _str(raw_stdout)
    stderr = _str(raw_stderr)
    returncode = proc.returncode if proc.returncode is not None else 0

    if check and returncode != 0:
        raise subprocess.CalledProcessError(returncode, list(cmd), stdout, stderr)

    return GitRunResult(args=cmd, returncode=returncode, stdout=stdout, stderr=stderr)


__all__ = ["GitRunResult", "run_git"]
