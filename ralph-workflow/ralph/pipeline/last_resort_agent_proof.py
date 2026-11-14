from __future__ import annotations

from dataclasses import dataclass

__all__ = ["LastResortAgentProof"]


@dataclass(frozen=True, slots=True)
class LastResortAgentProof:
    phase: str
    agent: str
    tried: bool
    outcome: str
    cooldown_until_ms: int | None
    reason: str | None
