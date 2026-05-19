"""Fallover event emitted when an agent chain advances to the next agent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class FalloverEvent:
    """Emitted when an agent is exhausted and the chain falls over to the next."""

    timestamp: datetime
    phase: str
    from_agent: str
    to_agent: str
    reason: str

    @classmethod
    def now(cls, *, phase: str, from_agent: str, to_agent: str, reason: str) -> FalloverEvent:
        return cls(
            timestamp=datetime.now(UTC),
            phase=phase,
            from_agent=from_agent,
            to_agent=to_agent,
            reason=reason,
        )
