"""Bounded dev-agent budget for repeatedly unresolvable integration conflicts.

Auto-integration runs at four seams: after every commit, at every clean
phase boundary (eleven events), at the fan-out join and at run startup.
When the rebase conflicts, the endpoint merge conflicts and the
dev-agent conflict resolver cannot repair it, nothing about the
repository changes -- so the very next seam repeats the identical
sequence, paying up to ``_MAX_RESOLVER_AGENTS`` full agent invocations
that SHARE one ``auto_integrate_resolve_timeout_seconds`` ceiling
(900 s by default) -- shared per seam, but a fresh ceiling every seam.
Nothing bounded that repetition and nothing escalated, so a single
unresolvable conflict made the run look stalled.

This module owns the bound and nothing else:

* :data:`MAX_CONSECUTIVE_RESOLVER_ATTEMPTS` -- how many CONSECUTIVE
  unresolved conflicts against the same target may pay for an agent.
* :class:`ConflictIdentity` -- the observation pair that decides
  whether the next seam is looking at the SAME unresolved conflict.
* :func:`resolver_allowed` -- the predicate the integration seam
  consults before handing the resolver down.
* :func:`apply_conflict_budget` -- carries the counter forward onto the
  outcome, resets it on any successful land, and rewrites the recorded
  reason to name the exhausted budget and the blocked target when the
  agent was suppressed.

The budget only reduces how OFTEN the agent is invoked; it never widens
what the agent may do, and it never suppresses the endpoint merge. When
the budget is exhausted the seam passes ``conflict_resolver=None``, so
the merge still runs and still aborts cleanly and the feature branch is
left bit-identical -- the contract
``tests/test_auto_integrate.py::test_both_conflicted_leaves_feature_bit_identical``
pins for the no-resolver case.

The counter is CONSECUTIVE by design: any successful land resets it to
zero, so a transient conflict can never permanently consume the budget.
Run startup passes a fresh :class:`RebaseState`, which deliberately
grants one clean attempt per run.

The counter is also scoped to ONE conflict, not merely to one target
branch NAME. The mainline this run integrates onto is shared: other
agents move its tip continuously, and the developer keeps adding
feature commits between seams. A budget keyed only on ``last_target``
would therefore keep suppressing the resolver for a conflict that no
longer exists -- new feature work against ``main`` would inherit the
exhausted budget of an older, unrelated conflict and never be offered
to the agent at all. :class:`ConflictIdentity` closes that: the count
is carried forward only while the feature tip AND the target tip are
both unchanged, which is exactly the situation in which re-invoking
the agent would replay work it has already failed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.rebase_state import RebaseState

#: Consecutive unresolved conflicts against the same target that may
#: each pay for a dev-agent conflict resolution. The third and later
#: attempts record an escalation instead of invoking the agent.
MAX_CONSECUTIVE_RESOLVER_ATTEMPTS = 2

_ACTION_CONFLICT = "conflict"

__all__ = [
    "MAX_CONSECUTIVE_RESOLVER_ATTEMPTS",
    "ConflictIdentity",
    "apply_conflict_budget",
    "prior_conflict_count",
    "resolver_allowed",
]


@dataclass(frozen=True, slots=True)
class ConflictIdentity:
    """The (feature tip, target tip) pair that identifies one conflict.

    Observed by the integration seam immediately before it attempts
    the rebase, and recorded on :class:`RebaseState` whenever the
    attempt ends in an unresolved conflict. The next seam compares its
    own observation against the recorded one: equal means "the same
    conflict the agent already failed on", so the carried count still
    applies; different means new work on either side, so the budget
    starts over.

    An unobservable side (``None`` -- ``git rev-parse`` failed, or a
    legacy checkpoint written before these fields existed) is treated
    as MATCHING. That direction is deliberate: it preserves the bound
    rather than handing an unbounded supply of 900 s agent invocations
    to a repository we cannot even read a SHA from.
    """

    feature_sha: str | None = None
    target_sha: str | None = None

    def matches(self, state: RebaseState) -> bool:
        """Whether ``state`` recorded a conflict with this same identity."""
        recorded = (state.last_conflict_feature_sha, state.last_conflict_target_sha)
        observed = (self.feature_sha, self.target_sha)
        if None in recorded or None in observed:
            return True
        return recorded == observed


#: Identity for callers that cannot observe the tips (and for the
#: pure-state unit paths); compares as matching, preserving the bound.
_UNKNOWN_IDENTITY = ConflictIdentity()


def prior_conflict_count(
    state: RebaseState,
    target: str,
    identity: ConflictIdentity = _UNKNOWN_IDENTITY,
) -> int:
    """Consecutive unresolved conflicts carried in from the previous seam.

    The count only applies to the SAME conflict, which takes two
    checks:

    * the SAME target -- an integration that switches mainline (a
      reconfigured ``auto_integrate_target``, or a detected default
      that changed) is a different conflict; and
    * the SAME endpoints -- a new feature commit, or a mainline tip
      another agent has since moved, is also a different conflict even
      though the branch name did not change.

    Either difference starts with a full budget.
    """
    if state.last_target != target:
        return 0
    if not identity.matches(state):
        return 0
    return max(0, state.consecutive_conflicts)


def resolver_allowed(
    state: RebaseState,
    target: str,
    identity: ConflictIdentity = _UNKNOWN_IDENTITY,
) -> bool:
    """Whether this attempt may invoke the dev-agent conflict resolver.

    True for the first :data:`MAX_CONSECUTIVE_RESOLVER_ATTEMPTS`
    consecutive unresolved conflicts against the same ``target`` AND
    the same ``identity``. A fresh :class:`RebaseState` always yields
    True, so the first attempt of a run is never suppressed.
    """
    return (
        prior_conflict_count(state, target, identity)
        < MAX_CONSECUTIVE_RESOLVER_ATTEMPTS
    )


def apply_conflict_budget(
    record: RebaseState,
    *,
    prior: RebaseState,
    target: str,
    resolver_suppressed: bool,
    identity: ConflictIdentity = _UNKNOWN_IDENTITY,
) -> RebaseState:
    """Carry the conflict counter onto ``record`` and escalate when exhausted.

    * A successful land resets the counter to zero and clears the
      recorded conflict identity.
    * A recorded conflict increments the count (from zero when
      ``identity`` shows this is a different conflict) and stamps
      ``identity`` onto the outcome so the next seam can tell whether
      it is looking at the same conflict.
    * Any other outcome (a skip, for instance) carries the prior count
      AND the prior identity forward unchanged, so an unrelated skip
      neither consumes nor forgives the budget.

    When ``resolver_suppressed`` is True and the outcome is still a
    conflict, the recorded reason is rewritten to name the exhausted
    budget and the blocked target, which is what turns an apparent
    stall into an actionable operator message.
    """
    carried = prior_conflict_count(prior, target, identity)
    if record.fast_forwarded:
        return record.model_copy(
            update={
                "consecutive_conflicts": 0,
                "last_conflict_feature_sha": None,
                "last_conflict_target_sha": None,
            }
        )
    if record.last_action != _ACTION_CONFLICT:
        return record.model_copy(
            update={
                "consecutive_conflicts": carried,
                "last_conflict_feature_sha": prior.last_conflict_feature_sha,
                "last_conflict_target_sha": prior.last_conflict_target_sha,
            }
        )

    update: dict[str, object] = {
        "consecutive_conflicts": carried + 1,
        "last_conflict_feature_sha": identity.feature_sha,
        "last_conflict_target_sha": identity.target_sha,
    }
    if resolver_suppressed:
        update["last_reason"] = (
            f"conflict resolution budget exhausted for '{target}' after "
            f"{carried} unresolved attempts; agent resolution suppressed "
            f"until an integration lands"
        )
    return record.model_copy(update=update)
