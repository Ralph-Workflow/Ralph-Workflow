"""Phase-boundary gates for auto-integration: target, cleanliness, freshness.

Split out of :mod:`ralph.pipeline.auto_integrate` so that module stays
inside the repo-structure size limit. These four helpers form one
coherent unit -- everything that decides whether a PHASE BOUNDARY (as
opposed to a commit) has anything to integrate -- and they depend only
on imported collaborators, never on anything ``auto_integrate`` defines,
so the dependency runs one way and no import cycle is possible.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import branch_sha, is_ancestor
from ralph.git.operations import get_head_sha
from ralph.pipeline._auto_integrate_config import (
    configured_target as _configured_target,
)
from ralph.pipeline._auto_integrate_config import (
    missing_target_reason as _missing_target_reason,
)
from ralph.pipeline.auto_integrate_boundary_refresh import BOUNDARY_REFRESH_THROTTLE
from ralph.pipeline.auto_integrate_context import record_refresh, record_when_stale
from ralph.pipeline.auto_integrate_ff import retry_pending_remote_publish
from ralph.pipeline.auto_integrate_outcome import record_skip as _record_skip
from ralph.pipeline.auto_integrate_refresh import refresh_target as _refresh_target
from ralph.pipeline.auto_integrate_remote_sync import (
    REMOTE_PUSH_REJECTED,
    pull_and_reconcile_target,
    remote_sync_enabled,
)
from ralph.pipeline.auto_integrate_sync import REFRESH_SUPPRESSED
from ralph.pipeline.auto_integrate_worktree_state import _worktree_is_clean

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.rebase_state import RebaseState

__all__ = [
    "boundary_freshness_outcome",
    "defer_dirty_boundary",
    "phase_boundary_outcome",
    "target_is_ahead",
]


def phase_boundary_outcome(
    config: UnifiedConfig,
    root: Path,
    target: str | None,
    state: RebaseState,
    *,
    rebase_stop_resolver: RebaseStopResolver | None,
) -> tuple[bool, RebaseState | None]:
    """Return whether the target, cleanliness, or freshness gate ends this boundary."""
    if target is None:
        return True, _record_skip(reason=_missing_target_reason(config), target=_configured_target(config))
    if not _worktree_is_clean(root):
        return True, defer_dirty_boundary(config, root, target)
    outcome = boundary_freshness_outcome(
        config, root, target, state, rebase_stop_resolver=rebase_stop_resolver
    )
    return outcome is not None, outcome


def boundary_freshness_outcome(
    config: UnifiedConfig,
    root: Path,
    target: str,
    state: RebaseState,
    *,
    rebase_stop_resolver: RebaseStopResolver | None,
) -> RebaseState | None:
    """Return a freshness-gated no-commit boundary result, if the seam is done."""
    remote_record = pull_and_reconcile_target(
        config, root, target, rebase_stop_resolver=rebase_stop_resolver, prior=state
    )
    if remote_record is not None and not remote_record.freshness_safe:
        return remote_record
    refresh = None if remote_sync_enabled(config) else _refresh_target(config, root, target)
    if branch_sha(root, target) != get_head_sha(root):
        return None
    pending = retry_pending_remote_publish(config, root, target, state, rebase_stop_resolver=rebase_stop_resolver)
    if pending is not None or state.last_remote_sync != REMOTE_PUSH_REJECTED:
        if pending is not None:
            return pending
        if remote_record is not None:
            return remote_record.model_copy(
                update={
                    "last_reason": "no commits beyond target",
                    "last_action": "skipped",
                }
            )
        return record_when_stale(_record_skip(reason="no commits beyond target", target=target), refresh)
    return None


def target_is_ahead(root: Path, target_sha: str | None) -> bool:
    """Return whether the target carries commits HEAD does not have."""
    if target_sha is None:
        return False
    return not is_ancestor(root, target_sha, get_head_sha(root))


def defer_dirty_boundary(config: UnifiedConfig, root: Path, target: str) -> RebaseState | None:
    """Defer dirty-boundary integration, recording only meaningful missed catch-up."""
    refresh = REFRESH_SUPPRESSED
    if BOUNDARY_REFRESH_THROTTLE.should_refresh(root, target):
        refresh = _refresh_target(config, root, target)
        BOUNDARY_REFRESH_THROTTLE.record_outcome(root, target, refresh)
    target_sha = branch_sha(root, target)
    diverged = target_is_ahead(root, target_sha)
    if (
        diverged
        and refresh == REFRESH_SUPPRESSED
        and BOUNDARY_REFRESH_THROTTLE.should_force_refresh(root, target)
    ):
        refresh = _refresh_target(config, root, target)
        BOUNDARY_REFRESH_THROTTLE.record_forced_outcome(root, target, refresh)
        target_sha = branch_sha(root, target)
        diverged = target_is_ahead(root, target_sha)
    if diverged:
        skip = _record_skip(
            reason=(
                "worktree not clean; uncommitted tracked changes deferred catch-up integration"
            ),
            target=target,
        )
        return record_refresh(skip, refresh)
    # R2/AC8: ladder rung 3 -- this routine clean-target deferral has no
    # pending integration to report; a later clean seam re-evaluates live refs.
    logger.info(
        "auto_integrate: phase-transition integration deferred; worktree dirty (target '{}')",
        target,
    )
    return None
