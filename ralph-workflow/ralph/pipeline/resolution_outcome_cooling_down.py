from __future__ import annotations

from dataclasses import dataclass

__all__ = ["ResolversCoolingDown"]


@dataclass(frozen=True, slots=True)
class ResolversCoolingDown:
    agents: tuple[str, ...] = ()
