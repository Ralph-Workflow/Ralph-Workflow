"""PTY spawn option type for ProcessManager."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PtySpawnOptions:
    """Options for spawning a PTY-backed child process."""

    cwd: str | None = None
    env: dict[str, str] | None = None
    cols: int = 80
    rows: int = 24
    label: str | None = None
