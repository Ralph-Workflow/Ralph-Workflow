
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
    ) -> tuple[bool, str | None, ResolutionFailureCause]:
        ...


class _LiveRebaseContinuationExecutor:

    def continue_rebase(
        self,
        root: Path,
        target: str,
        resolver: RebaseStopResolver,
        config: ConflictResolutionConfig | None,
    ) -> tuple[bool, str | None, ResolutionFailureCause]:
        from ralph.pipeline._auto_integrate_rebase_resolve import _RebaseResolutionOutcome
        from ralph.pipeline.auto_integrate_rebase_merge import _resolve_rebase_with_config

        outcome = _resolve_rebase_with_config(root, target, resolver, config)
        failure_cause = ResolutionFailureCause.NONE
        if isinstance(outcome, _RebaseResolutionOutcome):
            failure_cause = outcome.failure_cause
        return (bool(outcome[0]), outcome[1], failure_cause)


_LIVE_REBASE_CONTINUATION_EXECUTOR = _LiveRebaseContinuationExecutor()


def default_rebase_continuation_executor() -> RebaseContinuationExecutor:
    return _LIVE_REBASE_CONTINUATION_EXECUTOR
