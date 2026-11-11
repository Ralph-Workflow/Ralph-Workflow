
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SizeCheckResult:

    level: str
    message: str | None = None
