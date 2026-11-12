from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PtySpawnOptions:
    cwd: str | None = None
    env: dict[str, str] | None = None
    cols: int = 80
    rows: int = 24
    label: str | None = None
