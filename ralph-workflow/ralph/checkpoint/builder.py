"""Builder helpers for Python checkpoint payload extensions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypedDict, cast

from ralph.pipeline.progress import derive_run_context_progress

from .execution_history import ExecutionHistory, ExecutionHistoryDict
from .run_context import RunContext, RunContextDict

if TYPE_CHECKING:
    from ralph.pipeline.state import PipelineState


class CheckpointPayloadDict(TypedDict):
    state: dict[str, object]
    run_context: RunContextDict
    execution_history: ExecutionHistoryDict
    working_dir: str
    phase: str


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

    def to_dict(self) -> CheckpointPayloadDict:
        """Return a JSON-safe dictionary representation."""
        payload: CheckpointPayloadDict = {
            "state": cast("dict[str, object]", self.state.model_dump(mode="json")),
            "run_context": self.run_context.to_dict(),
            "execution_history": self.execution_history.to_dict(),
            "working_dir": self.working_dir,
            "phase": self.phase,
        }
        return payload


@dataclass(frozen=True)
class CheckpointBuilder:
    """Builder for assembling enriched Python checkpoint payloads."""

    _state: PipelineState | None = None
    _run_context: RunContext | None = None
    _execution_history: ExecutionHistory = field(default_factory=ExecutionHistory.new)
    _working_dir: str = ""

    @classmethod
    def new(cls) -> CheckpointBuilder:
        """Create a fresh checkpoint builder."""
        return cls()

    def state(self, state: PipelineState) -> CheckpointBuilder:
        """Attach the pipeline state."""
        return type(self)(
            _state=state,
            _run_context=self._run_context,
            _execution_history=self._execution_history,
            _working_dir=self._working_dir,
        )

    def run_context(self, run_context: RunContext) -> CheckpointBuilder:
        """Attach run lineage metadata."""
        return type(self)(
            _state=self._state,
            _run_context=run_context,
            _execution_history=self._execution_history,
            _working_dir=self._working_dir,
        )

    def execution_history(self, execution_history: ExecutionHistory) -> CheckpointBuilder:
        """Attach bounded execution history."""
        return type(self)(
            _state=self._state,
            _run_context=self._run_context,
            _execution_history=execution_history,
            _working_dir=self._working_dir,
        )

    def working_dir(self, working_dir: str) -> CheckpointBuilder:
        """Attach the working directory captured for the checkpoint."""
        return type(self)(
            _state=self._state,
            _run_context=self._run_context,
            _execution_history=self._execution_history,
            _working_dir=working_dir,
        )

    def build(self) -> CheckpointPayload:
        """Build the checkpoint payload or raise if required state is missing."""
        if self._state is None:
            raise ValueError("CheckpointBuilder requires pipeline state before build()")

        run_context = self._run_context or RunContext.new()
        normalized_context = derive_run_context_progress(self._state, run_context)

        return CheckpointPayload(
            state=self._state,
            run_context=normalized_context,
            execution_history=self._execution_history,
            working_dir=self._working_dir,
        )
