"""Git-observing seam between auto-integration and its conflict budget.

Separated from :mod:`ralph.pipeline.auto_integrate` so that module
stays under the repo-structure ``_MAX_FILE_LINES`` cap, and kept out of
:mod:`ralph.pipeline.auto_integrate_conflict_budget` so that module
stays a pure, trivially unit-testable policy: it decides the bound, it
never touches a repository. This module is the other half -- the two
places where the bound needs to look at (or reason about) real git
state.

* :func:`observe_conflict_identity` reads the endpoint pair that
  identifies the conflict an attempt is about to try to reconcile.
* :func:`carry_budget_through_skip` keeps that bound intact across an
  early skip, which short-circuits before the budget is consulted.
* :func:`charge_failed_attempt` keeps it intact across an unexpected
  failure, which short-circuits before the budget is applied.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import branch_sha
from ralph.git.operations import GitOperationError, get_head_sha
from ralph.pipeline.auto_integrate_conflict_budget import (
    ConflictIdentity,
    apply_conflict_budget,
    prior_conflict_count,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.rebase_state import RebaseState

__all__ = [
    "carry_budget_through_skip",
    "charge_failed_attempt",
    "observe_conflict_identity",
]


def observe_conflict_identity(root: Path, target: str) -> ConflictIdentity:
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
    return ConflictIdentity(
        feature_sha=feature_sha,
        target_sha=branch_sha(root, target),
    )


def carry_budget_through_skip(
    skip: RebaseState, *, prior: RebaseState
) -> RebaseState:
    """Preserve the conflict budget across an early skip.

    An early skip (dirty worktree, failed preconditions, ...)
    short-circuits before the budget is consulted, so without this the
    freshly built skip record would carry the model defaults --
    ``consecutive_conflicts=0`` and no identity -- and REFUND the
    budget. A dirty worktree between two seams would then buy the
    resolver another 900 s attempt at a conflict it has already failed,
    which is the unbounded behaviour the budget exists to stop. A skip
    changes nothing about the repository, so it must neither consume
    nor forgive the budget.

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
    )


def charge_failed_attempt(
    skip: RebaseState,
    *,
    prior: RebaseState,
    target: str,
    identity: ConflictIdentity,
    resolver_offered: bool,
) -> RebaseState:
    """Account for an integration attempt that died with an exception.

    An unexpected failure is turned into a skip record built from the
    model defaults -- ``consecutive_conflicts=0`` and no identity --
    which REFUNDS the budget. The dev-agent conflict resolver runs
    inside the guarded region, so a resolver that raises would
    otherwise zero the count at every seam and the bound would never
    engage: exactly the unbounded 900 s-per-seam behaviour the budget
    exists to stop.

    When a resolver was actually handed down, the failure is therefore
    CHARGED as a consumed attempt -- the seam paid for the agent and
    did not land, which is what the budget counts. With no resolver in
    play nothing was paid for, so the prior count is merely carried.
    Either way this is a bound on agent invocations only; the rebase
    and the endpoint merge still run at the next seam, and any
    successful land still resets the count to zero.
    """
    carried = prior_conflict_count(prior, target, identity)
    return skip.model_copy(
        update={
            "consecutive_conflicts": carried + 1 if resolver_offered else carried,
            "last_conflict_feature_sha": identity.feature_sha,
            "last_conflict_target_sha": identity.target_sha,
        }
    )
