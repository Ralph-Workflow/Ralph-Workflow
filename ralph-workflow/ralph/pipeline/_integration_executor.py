"""The injectable executor seam for the integration conflict-resolution steps.

The integration boundary drives real mutations (continuing a paused
rebase, completing a merge, resolving a conflicted index). Those
mutations are the only part of the boundary that touches the
repository, so they sit behind ONE narrow protocol: a caller injects
the executor it wants, and the production default drives live Git.

Why a protocol instead of a direct call
---------------------------------------

A direct call to the canonical rebase-continuation routine is not
black-box testable: exercising it means a real paused rebase, a real
resolver, and a real subprocess. With the executor injectable, a test
supplies a double that answers ``(resolved, reason)`` and the seam's
control flow -- which resolver runs, which branch is taken, what the
caller does with the answer -- is observable without a repository.

The production adapter is deliberately thin: it delegates to the
canonical routine
(:func:`ralph.pipeline.auto_integrate_rebase_merge._resolve_rebase_with_config`)
so there is exactly one implementation of "continue a paused rebase".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

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
    """Executes ONE paused-rebase continuation against a worktree."""

    def continue_rebase(
        self,
        root: Path,
        target: str,
        resolver: RebaseStopResolver,
        config: ConflictResolutionConfig | None,
    ) -> tuple[bool, str | None]:
        """Drive the paused rebase at ``root`` onto ``target``.

        Returns:
            ``(resolved, reason)``. ``resolved`` is ``True`` only when
            the rebase completed. ``reason`` carries the terminal
            resolver-chain evidence when the rebase did not complete,
            and is ``None`` when it did.
        """
        ...


class _LiveRebaseContinuationExecutor:
    """Production adapter: the canonical rebase-continuation routine."""

    def continue_rebase(
        self,
        root: Path,
        target: str,
        resolver: RebaseStopResolver,
        config: ConflictResolutionConfig | None,
    ) -> tuple[bool, str | None]:
        """Delegate to the canonical continuation routine.

        The import is resolved lazily so this module stays free of an
        import cycle with the auto-integrate package that implements
        the routine.
        """
        from ralph.pipeline.auto_integrate_rebase_merge import _resolve_rebase_with_config

        return _resolve_rebase_with_config(root, target, resolver, config)


_LIVE_REBASE_CONTINUATION_EXECUTOR = _LiveRebaseContinuationExecutor()


def default_rebase_continuation_executor() -> RebaseContinuationExecutor:
    """Return the production rebase-continuation executor (live Git)."""
    return _LIVE_REBASE_CONTINUATION_EXECUTOR
