from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ProcessState:
    returncode: int | None = None
    terminated: bool = False
    killed: bool = False
