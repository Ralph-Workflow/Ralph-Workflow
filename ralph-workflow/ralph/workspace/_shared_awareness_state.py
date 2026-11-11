
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["SharedAwarenessState"]


@dataclass(frozen=True)
class SharedAwarenessState:

    epoch: int
    paths: list[str]
    overflowed: bool
    owner_id: str
    changed: bool
