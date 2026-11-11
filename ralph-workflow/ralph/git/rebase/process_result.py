
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessResult:

    returncode: int
    stdout: str
    stderr: str

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0
