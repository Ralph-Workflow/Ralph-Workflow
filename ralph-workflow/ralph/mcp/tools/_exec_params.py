from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecParams:
    command: str
    args: list[str]
    timeout_ms: int
    shell_command: str | None = None
    max_runtime_ms: int | None = None
