"""Checkpoint payload model combining state and metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ralph.checkpoint.execution_history import ExecutionHistory

if TYPE_CHECKING:
    from ralph.checkpoint.run_context import RunContext
    from ralph.pipeline.state import PipelineState


@dataclass(frozen=True)
class CheckpointPayload:
    """Checkpoint payload combining pipeline state with extension metadata."""

    state: PipelineState
    run_context: RunContext
    execution_history: ExecutionHistory = field(default_factory=ExecutionHistory.new)
    working_dir: str = ""

    @property
    def phase(self) -> str:
        """Expose the current phase directly for checkpoint summaries."""
        return self.state.phase

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe dictionary representation."""
        return {
            "state": self.state.model_dump(mode="json"),
            "run_context": self.run_context.to_dict(),
            "execution_history": self.execution_history.to_dict(),
            "working_dir": self.working_dir,
            "phase": self.phase,
        }
