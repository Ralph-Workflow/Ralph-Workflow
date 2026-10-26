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
from ralph.pipeline._planning_obstruction import (
    planning_obstruction_from_readiness,
)
from ralph.pipeline.auto_integrate_boundary_refresh import BOUNDARY_REFRESH_THROTTLE
from ralph.pipeline.auto_integrate_context import record_refresh, record_when_stale
from ralph.pipeline.auto_integrate_outcome import record_skip as _record_skip
from ralph.pipeline.auto_integrate_refresh import refresh_target as _refresh_target
from ralph.pipeline.auto_integrate_remote_sync import (
    REMOTE_ALREADY_CURRENT,
    REMOTE_PULL_FAILED,
    REMOTE_REMOTE_UNREACHABLE,
    RemoteSyncStatus,
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
    from collections.abc import Callable
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
    get_head_sha_fn: Callable[[Path | str], str] | None = None,
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
        return True, defer_dirty_boundary(
            config, root, target, readiness=readiness, get_head_sha_fn=get_head_sha_fn
        )
    outcome = boundary_freshness_outcome(
        config,
        root,
        target,
        state,
        rebase_stop_resolver=rebase_stop_resolver,
        get_head_sha_fn=get_head_sha_fn,
    )
    return outcome is not None, outcome


def boundary_freshness_outcome(
    config: UnifiedConfig,
    root: Path,
    target: str,
    state: RebaseState,
    *,
    rebase_stop_resolver: RebaseStopResolver | None,
    get_head_sha_fn: Callable[[Path | str], str] | None = None,
) -> RebaseState | None:
    """Return a freshness-gated no-commit boundary result, if the seam is done."""
    remote_record = pull_and_reconcile_target(
        config, root, target, rebase_stop_resolver=rebase_stop_resolver, prior=state
    )
    refresh = None if remote_sync_enabled(config) else _refresh_target(config, root, target)
    head_reader = get_head_sha_fn or get_head_sha
    if branch_sha(root, target) != head_reader(root):
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
    # * already current (``REMOTE_ALREADY_CURRENT``) -- overwrite
    #   the reconciler's prose with the silent-skip record; no push
    #   is owed.
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
    already_current = last_remote_sync_now in {
        REMOTE_ALREADY_CURRENT,
        RemoteSyncStatus.ALREADY_CURRENT.value,
    } or not remote_sync_enabled(config)
    is_transient_failure = last_remote_sync_now in {
        REMOTE_PULL_FAILED,
        REMOTE_REMOTE_UNREACHABLE,
        RemoteSyncStatus.PULL_FAILED.value,
        RemoteSyncStatus.REMOTE_UNREACHABLE.value,
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


def target_is_ahead(
    root: Path,
    target_sha: str | None,
    *,
    get_head_sha_fn: Callable[[Path | str], str] | None = None,
) -> bool:
    """Return whether the target carries commits HEAD does not have."""
    if target_sha is None:
        return False
    head_reader = get_head_sha_fn or get_head_sha
    return not is_ancestor(root, target_sha, head_reader(root))


def defer_dirty_boundary(
    config: UnifiedConfig,
    root: Path,
    target: str,
    *,
    readiness: TreeReadinessAssessment | None = None,
    get_head_sha_fn: Callable[[Path | str], str] | None = None,
) -> RebaseState | None:
    """Defer dirty-boundary integration, recording only meaningful missed catch-up.

    The deferral DECISION is typed and made before this function: the
    caller's :attr:`TreeReadinessAssessment.integration_ready` check plus
    the ``diverged`` probe below. The recorded skip carries the typed
    obstruction cause alongside the prose: the kind is derived from the
    readiness assessment through the producer-side seam
    :func:`~ralph.pipeline._planning_obstruction.planning_obstruction_from_readiness`
    and persisted on ``last_planning_obstruction``, so the run loop routes
    on the closed :class:`~ralph.pipeline._planning_obstruction.PlanningObstructionKind`
    vocabulary without ever parsing the reason string. When the readiness
    cause is not one of the canonical typed states the kind stays unset and
    the record routes through the legacy-checkpoint shim
    :func:`~ralph.pipeline._planning_obstruction.identify_planning_obstruction`,
    exactly like an old checkpoint. The reason-string canonicalization
    below is display/record prose only -- it is never a decision input,
    and BOTH candidate strings translate to non-``NONE`` kinds that route
    identically, so the canonicalization cannot change behavior.
    """
    if readiness is None:
        readiness = assess_tree_readiness(root)
    refresh = REFRESH_SUPPRESSED
    if BOUNDARY_REFRESH_THROTTLE.should_refresh(root, target):
        refresh = _refresh_target(config, root, target)
        BOUNDARY_REFRESH_THROTTLE.record_outcome(root, target, refresh)
    target_sha = branch_sha(root, target)
    diverged = target_is_ahead(root, target_sha, get_head_sha_fn=get_head_sha_fn)
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
        # Display-only canonicalization (see the docstring): the recorded
        # reason always names the cleanliness deferral so an operator
        # reading the durable record sees the same deferral phrase for
        # every blocked boundary. This substring check decides NO
        # behavior -- both the readiness reason it may keep and the
        # canonical phrase it may substitute translate, through the one
        # sanctioned prose-to-kind parser, to non-NONE obstruction kinds
        # that the run loop routes identically.
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
        # Carry the typed obstruction cause into the durable record. The
        # kind is decided typed upstream from the readiness assessment; the
        # prose above is canonicalized display text and is NEVER re-parsed
        # to recover it. An untypeable (non-canonical) readiness cause
        # leaves the field unset so the legacy-checkpoint shim routes the
        # record, exactly like an old checkpoint.
        kind = planning_obstruction_from_readiness(readiness)
        if kind is not None:
            skip = skip.model_copy(update={"last_planning_obstruction": kind.value})
        return record_refresh(skip, refresh)
    # R2/AC8: ladder rung 3 -- this routine clean-target deferral has no
    # pending integration to report; a later clean seam re-evaluates live refs.
    logger.info(
        "auto_integrate: phase-transition integration deferred; worktree dirty (target '{}')",
        target,
    )
    return None
