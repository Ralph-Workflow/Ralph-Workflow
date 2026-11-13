from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ralph.git.resolution_failure_cause import ResolutionFailureCause

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.conflict_resolution_config import ConflictResolutionConfig
    from ralph.pipeline.conflict_resolution import RebaseStopResolver

__all__ = [
    "RebaseContinuationExecutor",
    "default_rebase_continuation_executor",
]


@runtime_checkable
class RebaseContinuationExecutor(Protocol):
    def continue_rebase(
        self,
        root: Path,
        target: str,
        resolver: RebaseStopResolver,
        config: ConflictResolutionConfig | None,
    ) -> tuple[bool, str | None, ResolutionFailureCause]: ...


class _LiveRebaseContinuationExecutor:
    def continue_rebase(
        self,
        root: Path,
        target: str,
        resolver: RebaseStopResolver,
        config: ConflictResolutionConfig | None,
    ) -> tuple[bool, str | None, ResolutionFailureCause]:
        from ralph.pipeline._stop_resolution_entry import attempt_stop_resolution
        from ralph.pipeline.auto_integrate_resolve import resolution_gate_record
        from ralph.pipeline.rebase_resolution_result import RebaseResolutionResult
        from ralph.pipeline.stop_resolution_blocked import StopResolutionBlocked
        from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger

        outcome = attempt_stop_resolution(
            None,
            StopResolutionTrigger(
                name="rebase_stop",
                root=root,
                target=target,
                resolver=resolver,
                record=resolution_gate_record(root, target),
                conflict_config=config,
            ),
        )
        if isinstance(outcome, StopResolutionBlocked):
            return (False, outcome.reason, ResolutionFailureCause.UNRESOLVED_CONFLICT)
        if not isinstance(outcome, RebaseResolutionResult):
            return (
                False,
                "rebase stop resolution returned an invalid result",
                ResolutionFailureCause.CONTEXT_FAILURE,
            )
        return outcome.succeeded, outcome.reason, outcome.failure_cause


_LIVE_REBASE_CONTINUATION_EXECUTOR = _LiveRebaseContinuationExecutor()


def default_rebase_continuation_executor() -> RebaseContinuationExecutor:
    return _LIVE_REBASE_CONTINUATION_EXECUTOR
