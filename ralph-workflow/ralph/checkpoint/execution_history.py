"""Bounded checkpoint execution history models."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TypedDict


def _timestamp() -> str:
    return datetime.now(UTC).isoformat()


class StepOutcomeDict(TypedDict, total=False):
    """JSON-safe representation of a single step outcome for checkpointing."""

    kind: str
    output: str | None
    files_modified: list[str]
    exit_code: int | None
    recoverable: bool | None
    error: str | None
    completed: str | None
    remaining: str | None
    reason: str | None


@dataclass(frozen=True)
class StepOutcome:
    """Outcome metadata for a single execution step."""

    kind: str
    output: str | None = None
    files_modified: list[str] = field(default_factory=list)
    exit_code: int | None = None
    recoverable: bool | None = None
    error: str | None = None
    completed: str | None = None
    remaining: str | None = None
    reason: str | None = None

    @classmethod
    def success(
        cls,
        output: str | None = None,
        files_modified: list[str] | None = None,
    ) -> StepOutcome:
        """Create a success outcome."""
        return cls(
            kind="success",
            output=output,
            files_modified=files_modified or [],
            exit_code=0,
        )

    @classmethod
    def failure(cls, error: str, *, recoverable: bool) -> StepOutcome:
        """Create a failure outcome."""
        return cls(kind="failure", error=error, recoverable=recoverable)

    @classmethod
    def partial(cls, completed: str, remaining: str) -> StepOutcome:
        """Create a partial outcome."""
        return cls(kind="partial", completed=completed, remaining=remaining)

    @classmethod
    def skipped(cls, reason: str) -> StepOutcome:
        """Create a skipped outcome."""
        return cls(kind="skipped", reason=reason)

    def to_dict(self) -> StepOutcomeDict:
        """Return a JSON-safe dictionary representation."""
        return {
            "kind": self.kind,
            "output": self.output,
            "files_modified": list(self.files_modified),
            "exit_code": self.exit_code,
            "recoverable": self.recoverable,
            "error": self.error,
            "completed": self.completed,
            "remaining": self.remaining,
            "reason": self.reason,
        }


class ExecutionStepDict(TypedDict, total=False):
    """JSON-safe representation of a single execution step for checkpointing."""

    phase: str
    iteration: int
    step_type: str
    timestamp: str
    outcome: StepOutcomeDict
    agent: str | None
    duration_secs: int | None


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

    def to_dict(self) -> ExecutionStepDict:
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


class ExecutionHistoryDict(TypedDict):
    """JSON-safe representation of the full execution history for checkpointing."""

    steps: list[ExecutionStepDict]
    file_snapshots: dict[str, str]


@dataclass(frozen=True)
class ExecutionHistory:
    """Bounded execution history plus checkpoint-relevant file snapshots."""

    steps: tuple[ExecutionStep, ...] = ()
    file_snapshots: dict[str, str] = field(default_factory=dict)

    @classmethod
    def new(cls, file_snapshots: dict[str, str] | None = None) -> ExecutionHistory:
        """Create an empty execution history."""
        return cls(file_snapshots=file_snapshots or {})

    def add_step_bounded(self, step: ExecutionStep, limit: int) -> ExecutionHistory:
        """Return a copy with the step appended and bounded to the given limit."""
        if limit <= 0:
            bounded_steps: tuple[ExecutionStep, ...] = ()
        else:
            queue = deque(self.steps, maxlen=limit)
            queue.append(step)
            bounded_steps = tuple(queue)
        return ExecutionHistory(
            steps=bounded_steps,
            file_snapshots=dict(self.file_snapshots),
        )

    def clone_bounded(self, limit: int) -> ExecutionHistory:
        """Clone the history while keeping only the most recent steps."""
        if limit <= 0:
            bounded_steps: tuple[ExecutionStep, ...] = ()
        else:
            bounded_steps = self.steps[-limit:]
        return ExecutionHistory(
            steps=bounded_steps,
            file_snapshots=dict(self.file_snapshots),
        )

    def to_dict(self) -> ExecutionHistoryDict:
        """Return a JSON-safe dictionary representation."""
        return {
            "steps": [step.to_dict() for step in self.steps],
            "file_snapshots": dict(self.file_snapshots),
        }
