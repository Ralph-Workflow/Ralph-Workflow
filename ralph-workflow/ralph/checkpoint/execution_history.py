"""Bounded checkpoint execution history models."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.checkpoint.execution_step import ExecutionStep


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

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe dictionary representation."""
        return {
            "steps": [step.to_dict() for step in self.steps],
            "file_snapshots": dict(self.file_snapshots),
        }
