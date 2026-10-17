"""Post-integration head read and precondition reclaim helpers.

Split from :mod:`ralph.pipeline.auto_integrate` so the orchestrator
stays within the repo-structure file-size limit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.operations import get_head_sha
from ralph.git.rebase import RebasePreconditionError, check_rebase_preconditions
from ralph.pipeline._recovery_fast_forward_landing import (
    _record_skip as _recovery_record_skip,
)
from ralph.pipeline.auto_integrate_outcome import record_skip as _record_skip
from ralph.pipeline.auto_integrate_record import clear_record as _clear_record
from ralph.pipeline.auto_integrate_recovery_legacy import (
    _reclaim_unowned_stale_rebase,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.rebase_state import RebaseState


def _read_post_integration_head_sha(root: Path, target: str) -> str | None:
    """Read the feature branch's HEAD after a successful rebase/merge.

    Returns the SHA on success; clears the crash record and returns
    ``None`` on failure so the orchestrator can fall through to its
    final state construction without crashing.

    Extracted from :func:`_auto_integrate_after_commit_inner` so the
    orchestrator keeps a sensible return-statement count.
    """
    try:
        return get_head_sha(root)
    except Exception as exc:
        _clear_record(root)
        logger.warning("auto_integrate: get_head_sha failed post-merge: {}", exc)
        return None


@dataclass(frozen=True)
class PreconditionReclaimResult:
    """Typed result of worktree reclaim attempt at the skip seam."""

    succeeded: bool
    skip_state: RebaseState | None = None


def _reclaim_and_retry_preconditions(
    root: Path,
    target: str,
    precondition_exc: RebasePreconditionError,
) -> PreconditionReclaimResult:
    """Reclaim the integration worktree at the skip seam."""
    reclaimed = _reclaim_unowned_stale_rebase(root)
    if reclaimed is None:
        # No reclaim happened: either nothing to reclaim or the
        # tree was dirty (operator-owned, AC-11 case 4). Let the
        # caller record a skip with the original cause.
        return PreconditionReclaimResult(succeeded=False, skip_state=None)
    if reclaimed.recovery_record_retained:
        return PreconditionReclaimResult(succeeded=False, skip_state=reclaimed)
    logger.warning(
        "auto_integrate: rebase preconditions blocked by stale "
        "unowned state for target '{}': {}; "
        "reclaimed at seam (AC-07/R8) -- retrying preconditions",
        target,
        precondition_exc,
    )
    try:
        check_rebase_preconditions(root)
    except RebasePreconditionError as retry_exc:
        # Reclaim ran but the precondition still fails (e.g.
        # the state was operator-owned and the dirty-tree check
        # kept it). Surface the failure loudly with the
        # original cause so the next seam can retry; never a
        # silent noop.
        logger.warning(
            "auto_integrate: rebase preconditions still failing "
            "after reclaim at seam for target '{}': {} (was {}); "
            "the recovery preamble will reclaim on the next run",
            target,
            retry_exc,
            precondition_exc,
        )
        return PreconditionReclaimResult(
            succeeded=False,
            skip_state=_recovery_record_skip(
                reason=f"preconditions not met after reclaim: {retry_exc}",
                target=target,
            ),
        )
    return PreconditionReclaimResult(succeeded=True, skip_state=None)


def _handle_precondition_failure(
    root: Path,
    target: str,
    exc: RebasePreconditionError,
) -> RebaseState | None:
    reclaim_result = _reclaim_and_retry_preconditions(root, target, exc)
    if reclaim_result.succeeded:
        return None
    if reclaim_result.skip_state is not None:
        return reclaim_result.skip_state
    # A precondition failure disables auto-integration for the whole
    # run, so it must not be visible only as one token on an
    # activity line that scrolls past. WARN names the target and the
    # precondition so the cause is greppable in the run log; the
    # recorded skip keeps the never-crash semantics unchanged.
    logger.warning(
        "auto_integrate: rebase preconditions not met for target '{}': {}",
        target,
        exc,
    )
    return _record_skip(reason=f"preconditions not met: {exc}", target=target)


__all__ = ["PreconditionReclaimResult"]
