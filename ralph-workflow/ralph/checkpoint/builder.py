
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ralph.checkpoint.checkpoint_payload import CheckpointPayload
from ralph.checkpoint.execution_history import ExecutionHistory
from ralph.checkpoint.run_context import RunContext
from ralph.pipeline.progress import derive_run_context_progress

if TYPE_CHECKING:
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PipelinePolicy


@dataclass(frozen=True)
class CheckpointBuilder:

    _state: PipelineState | None = None
    _run_context: RunContext | None = None
    _execution_history: ExecutionHistory = field(default_factory=ExecutionHistory.new)
    _working_dir: str = ""
    _policy: PipelinePolicy | None = None

    @classmethod
    def new(cls) -> CheckpointBuilder:
        return cls()

    def pipeline_policy(self, policy: PipelinePolicy) -> CheckpointBuilder:
        return type(self)(
            _state=self._state,
            _run_context=self._run_context,
            _execution_history=self._execution_history,
            _working_dir=self._working_dir,
            _policy=policy,
        )

    def state(self, state: PipelineState) -> CheckpointBuilder:
        return type(self)(
            _state=state,
            _run_context=self._run_context,
            _execution_history=self._execution_history,
            _working_dir=self._working_dir,
            _policy=self._policy,
        )

    def run_context(self, run_context: RunContext) -> CheckpointBuilder:
        return type(self)(
            _state=self._state,
            _run_context=run_context,
            _execution_history=self._execution_history,
            _working_dir=self._working_dir,
            _policy=self._policy,
        )

    def execution_history(self, execution_history: ExecutionHistory) -> CheckpointBuilder:
        return type(self)(
            _state=self._state,
            _run_context=self._run_context,
            _execution_history=execution_history,
            _working_dir=self._working_dir,
            _policy=self._policy,
        )

    def working_dir(self, working_dir: str) -> CheckpointBuilder:
        return type(self)(
            _state=self._state,
            _run_context=self._run_context,
            _execution_history=self._execution_history,
            _working_dir=working_dir,
            _policy=self._policy,
        )

    def build(self) -> CheckpointPayload:
        if self._state is None:
            raise ValueError("CheckpointBuilder requires pipeline state before build()")

        run_context = self._run_context or RunContext.new()
        normalized_context = derive_run_context_progress(self._state, run_context, self._policy)

        return CheckpointPayload(
            state=self._state,
            run_context=normalized_context,
            execution_history=self._execution_history,
            working_dir=self._working_dir,
        )
