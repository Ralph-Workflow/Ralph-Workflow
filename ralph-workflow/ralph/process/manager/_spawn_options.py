"""Spawn option types for ProcessManager."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class SpawnOptions:
    """Options for spawning a synchronous or async child process.

    Contract: a child spawned by Ralph NEVER inherits Ralph's
    controlling-terminal stdin by default. ``stdin`` defaults to
    :data:`subprocess.DEVNULL` so an interactive child (Claude Code,
    etc.) cannot claim the foreground process group, put the shared
    TTY into raw mode, and steal keystrokes. Callers that genuinely
    need to write to the child must opt in explicitly with
    ``stdin=subprocess.PIPE`` (the only legitimate INHERIT-equivalent
    value, and it reads/writes through the same handle instead of
    Ralph's terminal).

    See ``tests/process/test_spawn_options_stdin_default.py`` for the
    in-process regression tests pinning this default and for the
    AST-scan that keeps every ``SpawnOptions(...)`` call site from
    re-introducing ``stdin=None`` (INHERIT).
    """

    cwd: str | None = None
    env: dict[str, str] | None = None
    stdin: int | None = subprocess.DEVNULL
    stdout: int | None = None
    stderr: int | None = None
    start_new_session: bool = True
    label: str | None = None
    text: bool = False
