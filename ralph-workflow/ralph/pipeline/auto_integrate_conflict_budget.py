"""Bounded dev-agent budget for repeatedly unresolvable integration conflicts.

Auto-integration runs at four seams: after every commit, at every clean
phase boundary (eleven events), at the fan-out join and at run startup.
When the rebase conflicts, the endpoint merge conflicts and the
dev-agent conflict resolver cannot repair it, nothing about the
repository changes -- so the very next seam repeats the identical
sequence, paying up to ``_MAX_RESOLVER_AGENTS`` full agent invocations
bounded at ``auto_integrate_resolve_timeout_seconds`` (900 s by
default) each time. Nothing bounded that and nothing escalated, so a
single unresolvable conflict made the run look stalled.

This module owns the bound and nothing else:

* :data:`MAX_CONSECUTIVE_RESOLVER_ATTEMPTS` -- how many CONSECUTIVE
  unresolved conflicts against the same target may pay for an agent.
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
"""

from __future__ import annotations

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
    "apply_conflict_budget",
    "prior_conflict_count",
    "resolver_allowed",
]


def prior_conflict_count(state: RebaseState, target: str) -> int:
    """Consecutive unresolved conflicts carried in from the previous seam.

    The count only applies to the SAME target: an integration that
    switches mainline (a reconfigured ``auto_integrate_target``, or a
    detected default that changed) is a different conflict and starts
    with a full budget.
    """
    if state.last_target != target:
        return 0
    return max(0, state.consecutive_conflicts)


def resolver_allowed(state: RebaseState, target: str) -> bool:
    """Whether this attempt may invoke the dev-agent conflict resolver.

    True for the first :data:`MAX_CONSECUTIVE_RESOLVER_ATTEMPTS`
    consecutive unresolved conflicts against ``target``. A fresh
    :class:`RebaseState` always yields True, so the first attempt of a
    run is never suppressed.
    """
    return prior_conflict_count(state, target) < MAX_CONSECUTIVE_RESOLVER_ATTEMPTS


def apply_conflict_budget(
    record: RebaseState,
    *,
    prior: RebaseState,
    target: str,
    resolver_suppressed: bool,
) -> RebaseState:
    """Carry the conflict counter onto ``record`` and escalate when exhausted.

    * A successful land resets the counter to zero.
    * A recorded conflict increments it.
    * Any other outcome (a skip, for instance) carries the prior count
      forward unchanged, so an unrelated skip neither consumes nor
      forgives the budget.

    When ``resolver_suppressed`` is True and the outcome is still a
    conflict, the recorded reason is rewritten to name the exhausted
    budget and the blocked target, which is what turns an apparent
    stall into an actionable operator message.
    """
    carried = prior_conflict_count(prior, target)
    if record.fast_forwarded:
        return record.model_copy(update={"consecutive_conflicts": 0})
    if record.last_action != _ACTION_CONFLICT:
        return record.model_copy(update={"consecutive_conflicts": carried})

    update: dict[str, object] = {"consecutive_conflicts": carried + 1}
    if resolver_suppressed:
        update["last_reason"] = (
            f"conflict resolution budget exhausted for '{target}' after "
            f"{carried} unresolved attempts; agent resolution suppressed "
            f"until an integration lands"
        )
    return record.model_copy(update=update)
