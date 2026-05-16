"""Execution step records for checkpoint history."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.checkpoint.step_outcome import StepOutcome


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class ExecutionStep:
    """Single history entry for checkpoint replay and auditing."""

    phase: str
    iteration: int
    step_type: str
    outcome: StepOutcome
    timestamp: str = field(default_factory=_timestamp)
    agent: str | None = None
    duration_secs: int | None = None

    @classmethod
    def new(
        cls,
        phase: str,
        iteration: int,
        step_type: str,
        outcome: StepOutcome,
    ) -> ExecutionStep:
        """Create a new execution step."""
        return cls(
            phase=phase,
            iteration=iteration,
            step_type=step_type,
            outcome=outcome,
        )

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe dictionary representation."""
        return {
            "phase": self.phase,
            "iteration": self.iteration,
            "step_type": self.step_type,
            "timestamp": self.timestamp,
            "outcome": self.outcome.to_dict(),
            "agent": self.agent,
            "duration_secs": self.duration_secs,
        }
