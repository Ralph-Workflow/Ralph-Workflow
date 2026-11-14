from __future__ import annotations

from dataclasses import dataclass

__all__ = ["LastResortAgentPool"]


@dataclass(frozen=True, slots=True)
class LastResortAgentPool:
    phase: str
    agents: tuple[str, ...]
