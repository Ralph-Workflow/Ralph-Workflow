
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _CompletedProcessAdapter:

    stdout: bytes
    stderr: bytes
    returncode: int
    truncated: bool = False
