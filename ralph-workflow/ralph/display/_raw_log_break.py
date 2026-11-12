from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RawLogBreak:
    kind: str
    offset: int
    detail: str


__all__ = ["RawLogBreak"]
