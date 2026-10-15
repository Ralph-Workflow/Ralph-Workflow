"""Checkpoint payload model combining state and metadata."""

from __future__ import annotations

from dataclasses import dataclass, field

from ralph.checkpoint.execution_history import ExecutionHistory
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

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> CheckpointPayload:
        """Construct CheckpointPayload from dictionary representation."""
        raw_state = data.get("state")
        if isinstance(raw_state, dict):
            state = PipelineState.model_validate(raw_state)
        else:
            state = PipelineState()

        raw_context = data.get("run_context")
        if isinstance(raw_context, dict):
            ctx_dict: dict[str, object] = {str(k): v for k, v in raw_context.items()}
            run_context = RunContext.from_dict(ctx_dict)
        else:
            run_context = RunContext.new()

        raw_history = data.get("execution_history")
        if isinstance(raw_history, dict):
            hist_dict: dict[str, object] = {str(k): v for k, v in raw_history.items()}
            execution_history = ExecutionHistory.from_dict(hist_dict)
        else:
            execution_history = ExecutionHistory.new()

        return cls(
            state=state,
            run_context=run_context,
            execution_history=execution_history,
            working_dir=str(data.get("working_dir", "")),
        )
