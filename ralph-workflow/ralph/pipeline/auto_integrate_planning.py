"""Mandatory planning integration, including dirty-worktree-safe landing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.branch_relation import branch_relation
from ralph.git.rebase.rebase import rebase_in_progress
from ralph.pipeline._planning_obstruction import identify_planning_obstruction
from ralph.pipeline.auto_integrate import _integrate_once, auto_integrate_after_commit
from ralph.pipeline.auto_integrate_catchup import resolve_integration_target
from ralph.pipeline.auto_integrate_outcome import record_conflict, record_skip
from ralph.pipeline.auto_integrate_recovery import (
    recover_incomplete_integration,
    recovery_retained_record,
)
from ralph.pipeline.commit_executor import (
    reattach_head_if_detached as _reattach_head_if_detached,
)
from ralph.pipeline.conflict_resolution import (
    resolution_session_from_config,
    resolve_rebase_in_progress,
)
from ralph.pipeline.integration_resolution import (
    TreeReadinessAssessment,
    assess_tree_readiness,
    retained_integration_reason,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope


def integrate_before_planning(
    config: UnifiedConfig,
    scope: WorkspaceScope,
    state: RebaseState,
    *,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    display: ParallelDisplay | None = None,
    readiness: TreeReadinessAssessment | None = None,
) -> RebaseState | None:
    """Land committed work without stashing or committing unfinished edits.

    A dirty tree can publish an already synchronized committed tip. Otherwise
    use Git's non-destructive merge refusal, with a clean index so the merge
    cannot sweep staged agent work into history.
    """
    root = scope.root
    if retained_integration_reason(root) is not None:
        from ralph.pipeline._integration_continuation import config_for_owned_integration

        config = config_for_owned_integration(config)
        recovered = recover_incomplete_integration(
            scope,
            config=config,
            conflict_resolver=conflict_resolver,
            rebase_stop_resolver=rebase_stop_resolver,
        )
        if recovery_retained_record(recovered) or (
            recovered is not None and recovered.fast_forwarded
        ):
            return recovered
    target = (
        resolve_integration_target(config, root) if config.general.auto_integrate_enabled else None
    )
    if target is None:
        return None
    unfinished = _finish_paused_rebase(config, root, target, rebase_stop_resolver)
    if unfinished is not None:
        return unfinished
    _reattach_head_if_detached(root, config=config, auto_integrate_target=target)
    if readiness is None:
        readiness = assess_tree_readiness(root)
    if readiness.integration_ready and not branch_relation(root, target).needs_catch_up:
        return auto_integrate_after_commit(
            config,
            scope,
            state,
            conflict_resolver=conflict_resolver,
            rebase_stop_resolver=rebase_stop_resolver,
            display=display,
        )
    staged_block = _record_staged_block(root, target, readiness=readiness)
    if staged_block is not None:
        return staged_block
    from ralph.pipeline.auto_integrate_recovery_terminal import post_attempt_verify

    post_attempt_verify(root, expected_head_sha=None, owns_resolution=False)
    return _integrate_with_force_merge(
        config,
        root,
        target,
        conflict_resolver,
        rebase_stop_resolver=rebase_stop_resolver,
        display=display,
    )


def _finish_paused_rebase(
    config: UnifiedConfig,
    root: Path,
    target: str,
    resolver: RebaseStopResolver | None,
) -> RebaseState | None:
    """Drive a rebase already underway to completion before planning.

    A run that starts inside a paused rebase (its own interrupted one or an
    operator's) must not plan on a detached, half-replayed tree, and must
    not wait for a human: every stop goes to the resolver. ``None`` means
    the rebase finished and normal integration continues; a conflict state
    means it did not, and the next entry offers the same stop again, so
    repeating the call is safe. ``None`` also when no rebase is paused.
    """
    if resolver is None or not rebase_in_progress(root):
        return None
    if resolve_rebase_in_progress(
        root, target, resolver, session=resolution_session_from_config(config)
    ):
        return None
    return record_conflict(
        reason="rebase already in progress could not be resolved; the resolver is asked again",
        target=target,
    )


def _record_staged_block(
    root: Path,
    target: str,
    *,
    readiness: TreeReadinessAssessment | None = None,
) -> RebaseState | None:
    """Record a typed planning obstruction for any non-ready tree.

    The planning, boundary, and integration-resolution gates all use
    :data:`assess_tree_readiness` as the canonical predicate (AC-12):
    for one tree state the three gates return the same
    ``integration_ready`` boolean and the same first
    ``blocking_reasons`` entry. The pre-fix code returned ``None``
    for any non-staged non-ready state, so planning fell through
    while boundary/resolution correctly blocked. That contradiction
    is the three-gates-disagree symptom the criteria call out.

    Returns ``None`` when ``readiness.integration_ready`` is True so
    the caller falls through to the normal integration path. For
    every other tree state the function records a typed
    :class:`RebaseState` with the readiness reason as
    ``last_reason`` and a closed-vocabulary
    ``last_planning_obstruction`` kind selected from the readiness
    reason via :func:`identify_planning_obstruction`. The
    ``STAGED_BLOCKS_INTEGRATION`` kind is the special case the
    run loop's force-merge path must NOT clobber; the closed
    ``identify_planning_obstruction`` vocabulary covers every other
    readiness reason (``local_overwrite``, ``untracked_overwrite``,
    or ``NONE`` for the catch-all).
    """
    if readiness is None:
        readiness = assess_tree_readiness(root)
    if readiness.integration_ready:
        return None
    reason = (
        readiness.blocking_reasons[0]
        if readiness.blocking_reasons
        else "worktree not clean; uncommitted tracked changes deferred catch-up integration"
    )
    # The single-source-of-truth predicate decides the verdict. The
    # kind is the closed-vocabulary routing signal the run loop reads;
    # the reason is the operator-facing free-form text.
    kind = identify_planning_obstruction(reason)
    recorded = record_skip(reason=reason, target=target)
    return recorded.model_copy(update={"last_planning_obstruction": kind.value})


def _integrate_with_force_merge(
    config: UnifiedConfig,
    root: Path,
    target: str,
    conflict_resolver: ConflictResolver | None,
    *,
    rebase_stop_resolver: RebaseStopResolver | None,
    display: ParallelDisplay | None,
) -> RebaseState | None:
    """Force a non-destructive endpoint merge for the planning integration."""
    outcome, _retry = _integrate_once(
        config,
        root,
        target,
        conflict_resolver,
        force_endpoint_merge=True,
        rebase_stop_resolver=rebase_stop_resolver,
        display=display,
    )
    return outcome
