"""GitRunResult: result of a git subprocess invocation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GitRunResult:
    """Result of a git subprocess invocation."""

    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
