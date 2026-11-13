from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Exhausted"]


@dataclass(frozen=True)
class Exhausted:
    agents: tuple[str, ...]
