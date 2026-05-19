"""Spawn option types for ProcessManager."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpawnOptions:
    """Options for spawning a synchronous or async child process."""

    cwd: str | None = None
    env: dict[str, str] | None = None
    stdin: int | None = None
    stdout: int | None = None
    stderr: int | None = None
    start_new_session: bool = True
    label: str | None = None
    text: bool = False
