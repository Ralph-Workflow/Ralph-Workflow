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
from ralph.pipeline.auto_integrate_outcome import record_skip as _record_skip
from ralph.pipeline.auto_integrate_refresh import refresh_target as _refresh_target
from ralph.pipeline.auto_integrate_remote_sync import (
    REMOTE_PULL_FAILED,
    REMOTE_REMOTE_UNREACHABLE,
    pull_and_reconcile_target,
    remote_sync_enabled,
)
from ralph.pipeline.auto_integrate_remote_sync import (
    reconcile_remote_publication as _reconcile_remote_publication,
)
from ralph.pipeline.auto_integrate_sync import REFRESH_SUPPRESSED
from ralph.pipeline.commit_executor import (
    reattach_head_if_detached as _reattach_head_if_detached,
)
from ralph.pipeline.integration_resolution import (
    TreeReadinessAssessment,
    assess_tree_readiness,
)

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
    readiness: TreeReadinessAssessment | None = None,
) -> tuple[bool, RebaseState | None]:
    """Return whether the target, cleanliness, or freshness gate ends this boundary."""
    if target is None:
        return True, _record_skip(
            reason=_missing_target_reason(config), target=_configured_target(config)
        )
    _reattach_head_if_detached(root, config=config, auto_integrate_target=target)
    if readiness is None:
        readiness = assess_tree_readiness(root)
    if not readiness.integration_ready:
        return True, defer_dirty_boundary(config, root, target, readiness=readiness)
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
    refresh = None if remote_sync_enabled(config) else _refresh_target(config, root, target)
    if branch_sha(root, target) != get_head_sha(root):
        return None
    # The phase-boundary seam runs the reconciler on every clean
    # boundary (not just when an in-memory flag is set). The OLD
    # ``last_push_status in {None, PUSHED, CREATED}`` filter is
    # gone: terminal state is now detected by the reconciler's
    # own ``local_sha == remote_sha`` short-circuit, not by an
    # in-memory status. The boundary returns the reconciler's
    # ``pending`` so the caller can observe the publication:
    # * transient failure (``REMOTE_REMOTE_UNREACHABLE`` /
    #   ``REMOTE_PULL_FAILED``) -- propagate ``pending`` so the
    #   next seam retries it (AC-8 defence-in-depth).
    # * already current (``"already current"``) -- overwrite the
    #   reconciler's prose with the silent-skip record; no push is
    #   owed.
    # * divergence -- propagate ``pending`` so the next seam
    #   observes the reintegration outcome.
    # The only ``None``-returning branch above is the HEAD-vs-target
    # feature-integration gate, which fires BEFORE the reconciler is
    # consulted.
    pending = _reconcile_remote_publication(
        config,
        root,
        target,
        rebase_stop_resolver=rebase_stop_resolver,
    )
    last_remote_sync_now = pending.last_remote_sync
    already_current = last_remote_sync_now == "already current" or not remote_sync_enabled(config)
    is_transient_failure = last_remote_sync_now in {
        REMOTE_PULL_FAILED,
        REMOTE_REMOTE_UNREACHABLE,
        "pull failed",
        "origin unreachable",
    }
    if is_transient_failure:
        # Transient failure (unreachable remote): preserve the
        # reconciler's verdict so the next seam retries it.
        return pending
    if already_current:
        # No commits to publish; the boundary is silent.
        if remote_record is not None:
            return remote_record.model_copy(
                update={
                    "last_reason": "no commits beyond target",
                    "last_action": "skipped",
                }
            )
        return record_when_stale(
            _record_skip(reason="no commits beyond target", target=target), refresh
        )
    return pending


def target_is_ahead(root: Path, target_sha: str | None) -> bool:
    """Return whether the target carries commits HEAD does not have."""
    if target_sha is None:
        return False
    return not is_ancestor(root, target_sha, get_head_sha(root))


def defer_dirty_boundary(
    config: UnifiedConfig,
    root: Path,
    target: str,
    *,
    readiness: TreeReadinessAssessment | None = None,
) -> RebaseState | None:
    """Defer dirty-boundary integration, recording only meaningful missed catch-up."""
    if readiness is None:
        readiness = assess_tree_readiness(root)
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
        reason = (
            readiness.blocking_reasons[0]
            if readiness and readiness.blocking_reasons
            else "worktree not clean; uncommitted tracked changes deferred catch-up integration"
        )
        if "worktree not clean" not in reason:
            reason = "worktree not clean; uncommitted tracked changes deferred catch-up integration"
        skip = _record_skip(
            reason=reason,
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
