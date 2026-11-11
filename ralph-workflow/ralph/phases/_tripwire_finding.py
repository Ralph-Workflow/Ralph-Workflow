
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:

    category: str
    message: str
    path: str | None = None
    count: int | None = None
