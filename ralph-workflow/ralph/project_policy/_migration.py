
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MigrationCandidate:

    path: str
    recognized_heading: str
    resolved: bool


__all__ = ["MigrationCandidate"]
