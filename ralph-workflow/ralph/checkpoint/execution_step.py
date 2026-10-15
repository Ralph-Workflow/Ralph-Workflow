"""Execution step records for checkpoint history."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

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

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ExecutionStep:
        """Construct ExecutionStep from dictionary representation."""
        raw_outcome = data.get("outcome")
        if isinstance(raw_outcome, dict):
            typed_outcome: dict[str, object] = {str(k): v for k, v in raw_outcome.items()}
            outcome = StepOutcome.from_dict(typed_outcome)
        else:
            outcome = StepOutcome.skipped("unknown")
        iteration_val = data.get("iteration")
        agent_val = data.get("agent")
        duration_val = data.get("duration_secs")
        return cls(
            phase=str(data.get("phase", "")),
            iteration=iteration_val if isinstance(iteration_val, int) else 0,
            step_type=str(data.get("step_type", "")),
            outcome=outcome,
            timestamp=str(data.get("timestamp", "")),
            agent=agent_val if isinstance(agent_val, str) else None,
            duration_secs=duration_val if isinstance(duration_val, int) else None,
        )
