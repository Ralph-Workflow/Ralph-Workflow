"""Git-observing seam between auto-integration and its conflict bookkeeping.

Separated from :mod:`ralph.pipeline.auto_integrate` so that module
stays under the repo-structure ``_MAX_FILE_LINES`` cap, and kept out of
:mod:`ralph.pipeline.auto_integrate_conflict_budget` so that module
stays a pure, trivially unit-testable policy: it decides the recorded
history, it never touches a repository. This module is the other half --
the two places where that history needs to look at (or reason about)
real git state.

* :func:`observe_conflict_identity` reads the endpoint pair that
  identifies the conflict an attempt is about to try to reconcile.
* :func:`carry_budget_through_skip` keeps that history intact across an
  early skip, which short-circuits before the bookkeeping is consulted.
* :func:`charge_failed_attempt` keeps it intact across an unexpected
  failure, which short-circuits before the bookkeeping is applied.

Resolver ELIGIBILITY is not decided here: the failure ledger owns it
(see :func:`~ralph.pipeline.auto_integrate_conflict_budget.resolver_allowed`).
This module only records what the repository did.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.errors import GitOperationError
from ralph.git.merge import branch_sha, conflict_stage_entries, unmerged_paths
from ralph.git.operations import get_head_sha
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.auto_integrate_conflict_budget import (
    MAX_TRACKED_CONFLICT_ATTEMPTS,
    ConflictIdentity,
    apply_conflict_budget,
    prior_conflict_count,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.rebase_state import RebaseState

__all__ = [
    "apply_completed_attempt_budget",
    "carry_budget_through_skip",
    "charge_failed_attempt",
    "observe_conflict_identity",
]


def observe_conflict_identity(
    root: Path, target: str, *, scope: str = "feature"
) -> ConflictIdentity:
    """Observe the endpoint pair that identifies one integration attempt.

    Read once per seam, before any git mutation, and threaded through
    the conflict budget so a conflict is only ever counted against the
    exact (feature tip, target tip) pair that produced it. See
    :class:`~ralph.pipeline.auto_integrate_conflict_budget.ConflictIdentity`
    for why an unreadable side is recorded as ``None`` rather than
    treated as a new conflict.
    """
    try:
        feature_sha: str | None = get_head_sha(root)
    except GitOperationError as exc:
        logger.debug("auto_integrate: conflict-identity HEAD read failed: {}", exc)
        feature_sha = None
    paths = tuple(path for path in unmerged_paths(root) if path != "<unmerged-path-query-failed>")
    entries = conflict_stage_entries(root, paths)
    stage_oids = tuple(
        f"{path}:{stage}:{blob}"
        for path in sorted(entries)
        for stage, (_mode, blob) in sorted(entries[path].items())
    )
    return ConflictIdentity(
        feature_sha=feature_sha,
        target_sha=branch_sha(root, target),
        conflicted_paths=paths,
        stage_oids=stage_oids,
        scope=scope,
    )


def carry_budget_through_skip(
    skip: RebaseState,
    *,
    prior: RebaseState,
    attempts: int = MAX_TRACKED_CONFLICT_ATTEMPTS,
) -> RebaseState:
    """Preserve the conflict bookkeeping across an early skip.

    An early skip (dirty worktree, failed preconditions, ...)
    short-circuits before the bookkeeping is consulted, so without this
    the freshly built skip record would carry the model defaults --
    ``consecutive_conflicts=0`` and no identity -- and REFUND the
    recorded history. A dirty worktree between two seams would then
    erase the record of a conflict the agent has already failed, which
    is the unbounded behaviour the bookkeeping exists to stop. A skip
    changes nothing about the repository, so it must neither consume
    nor forgive the recorded history.

    Skips with no resolved target (detached HEAD, AC-13) are returned
    untouched: there is no target to scope a count to.
    """
    if skip.last_target is None:
        return skip
    return apply_conflict_budget(
        skip,
        prior=prior,
        target=skip.last_target,
        resolver_suppressed=False,
        attempts=attempts,
    )


def apply_completed_attempt_budget(
    record: RebaseState,
    *,
    prior: RebaseState,
    target: str,
    identity: ConflictIdentity,
    resolver_suppressed: bool,
    attempts: int = MAX_TRACKED_CONFLICT_ATTEMPTS,
) -> RebaseState:
    """Charge only actual repository conflicts to the recorded history."""
    if record.last_failure_cause == ResolutionFailureCause.AGENT_FAILURE:
        return charge_failed_attempt(
            record, prior=prior, target=target, identity=identity, attempts=attempts
        )
    return apply_conflict_budget(
        record,
        prior=prior,
        target=target,
        resolver_suppressed=resolver_suppressed,
        identity=identity,
        attempts=attempts,
    )


def charge_failed_attempt(
    skip: RebaseState,
    *,
    prior: RebaseState,
    target: str,
    identity: ConflictIdentity,
    attempts: int = MAX_TRACKED_CONFLICT_ATTEMPTS,
) -> RebaseState:
    """Account for an integration attempt that died with an exception.

    An unexpected failure is turned into a skip record built from the
    model defaults -- ``consecutive_conflicts=0`` and no identity --
    which REFUNDS the recorded history. Preserve the existing count and
    identity: this guard covers Ralph and resolver invocation failures as
    well as repository conflicts, and only an observed
    ``last_action='conflict'`` is allowed to spend the recorded
    rebase-resolution history. Agent failures use the
    conflict-resolution recovery flow's cooldown and fallover instead.
    """
    carried = prior_conflict_count(prior, target, identity, attempts=attempts)
    updated = skip.record_consecutive_conflicts(carried, cap=attempts)
    return updated.model_copy(
        update={
            "last_conflict_feature_sha": identity.feature_sha,
            "last_conflict_target_sha": identity.target_sha,
            "last_conflict_paths": identity.conflicted_paths,
            "last_conflict_stage_oids": identity.stage_oids,
            "last_conflict_strategy_index": prior.conflict_strategy_index,
        }
    )
