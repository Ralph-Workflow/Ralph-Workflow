"""Bounded dev-agent budget for repeatedly unresolvable integration conflicts.

Auto-integration runs at four seams: after every commit, at every clean
phase boundary (eleven events), at the fan-out join and at run startup.
When the rebase conflicts, the endpoint merge conflicts and the
dev-agent conflict resolver cannot repair it, nothing about the
repository changes -- so the very next seam repeats the identical
sequence, paying up to ``_MAX_RESOLVER_AGENTS`` full agent invocations
that share one 900-second resolution ceiling -- shared per seam, but a
fresh ceiling every seam.
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

from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ralph.pipeline._integration_action import RebaseOutcomeKind

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ralph.pipeline.rebase_state import RebaseState
    from ralph.recovery.agent_failure_ledger import AgentFailureLedger

#: Consecutive unresolved conflicts against the same target that may
#: each pay for a dev-agent conflict resolution. The sixty-fifth and later
#: attempts record an escalation instead of invoking the agent.
MAX_CONSECUTIVE_RESOLVER_ATTEMPTS = 64

_ACTION_CONFLICT = RebaseOutcomeKind.CONFLICT.value

__all__ = [
    "MAX_CONSECUTIVE_RESOLVER_ATTEMPTS",
    "ConflictIdentity",
    "apply_conflict_budget",
    "finish_conflict_attempt",
    "prior_conflict_count",
    "resolver_allowed",
    "start_conflict_attempt",
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
    conflicted_paths: tuple[str, ...] = ()
    stage_oids: tuple[str, ...] = ()
    scope: str = "feature"

    def matches(self, state: RebaseState) -> bool:
        """Whether ``state`` recorded a conflict with this same identity."""
        recorded_scope = state.last_conflict_scope
        if self.scope and recorded_scope and self.scope != recorded_scope:
            return False
        if (
            self.conflicted_paths
            and state.last_conflict_paths
            and self.conflicted_paths != state.last_conflict_paths
        ):
            return False
        if (
            self.stage_oids
            and state.last_conflict_stage_oids
            and self.stage_oids != state.last_conflict_stage_oids
        ):
            return False
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
    *,
    attempts: int | None = None,
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
    if state.last_conflict_strategy_index != state.conflict_strategy_index:
        return 0
    count = max(0, state.consecutive_conflicts)
    if attempts is not None:
        return min(attempts, count)
    return count


def _ledger_candidates_available(
    ledger: AgentFailureLedger,
    phase: str,
    candidates: Sequence[str],
) -> bool:
    """Return True if any candidate in the ledger is available for phase."""
    if candidates:
        return any(ledger.is_available(phase, a) for a in candidates)
    snap = ledger.snapshot()
    raw_entries = snap.get("entries")
    if isinstance(raw_entries, dict):
        phase_agents: list[str] = []
        for val in raw_entries.values():
            if isinstance(val, dict):
                p = val.get("phase")
                a = val.get("agent")
                if isinstance(p, str) and p == phase and isinstance(a, str) and a:
                    phase_agents.append(a)
        if phase_agents:
            return any(ledger.is_available(phase, a) for a in phase_agents)
    return True


def resolver_allowed(
    state: RebaseState,
    target: str,
    identity: ConflictIdentity = _UNKNOWN_IDENTITY,
    *,
    ledger: AgentFailureLedger | None = None,
    candidates: Sequence[str] = (),
    phase: str = "rebase_conflict_resolution",
    attempts: int | None = None,
) -> bool:
    """Whether this attempt may invoke the dev-agent conflict resolver.

    Resolver selection and suppression is owned by ``AgentFailureLedger``:
    an attempt is allowed only when candidate resolvers are eligible/available
    in the failure ledger for ``phase``. Repository conflict identity logic
    is preserved: a changed conflict identity starts fresh.
    """
    effective_ledger = ledger
    if effective_ledger is None:
        try:
            from ralph.pipeline.conflict_resolution.rebase_loop import (
                active_rebase_resolution_session,
            )

            session = active_rebase_resolution_session()
            if session is not None and hasattr(session, "ledger"):
                effective_ledger = session.ledger
        except Exception:
            pass

    is_same_conflict = (
        state.last_target == target
        and identity.matches(state)
        and state.last_conflict_strategy_index == state.conflict_strategy_index
    )
    if is_same_conflict and state.resolution_exhausted:
        return False

    if effective_ledger is not None and hasattr(effective_ledger, "is_available"):
        return _ledger_candidates_available(effective_ledger, phase, candidates)

    effective_attempts = attempts if attempts is not None else MAX_CONSECUTIVE_RESOLVER_ATTEMPTS
    return prior_conflict_count(state, target, identity, attempts=effective_attempts) < effective_attempts


@runtime_checkable
class _SupportsMonotonic(Protocol):
    def monotonic(self) -> float: ...


@runtime_checkable
class _SupportsTime(Protocol):
    def time(self) -> float: ...


@runtime_checkable
class _SupportsCallSeconds(Protocol):
    def __call__(self) -> float: ...


def _read_clock_seconds(clock: object) -> float:
    """Read current logical seconds from an injected clock abstraction."""
    if clock is None:
        raise TypeError("clock must not be None; provide an injected clock abstraction")
    if isinstance(clock, _SupportsMonotonic):
        try:
            return float(clock.monotonic())
        except (TypeError, ValueError) as exc:
            raise TypeError(f"clock.monotonic() failed to return numeric seconds: {exc}") from exc
    if isinstance(clock, _SupportsTime):
        try:
            return float(clock.time())
        except (TypeError, ValueError) as exc:
            raise TypeError(f"clock.time() failed to return numeric seconds: {exc}") from exc
    if isinstance(clock, _SupportsCallSeconds):
        try:
            return float(clock())
        except (TypeError, ValueError) as exc:
            raise TypeError(f"clock callable failed to return numeric seconds: {exc}") from exc
    raise TypeError(
        f"Injected clock {type(clock).__name__!r} must be callable or provide .monotonic() or .time()"
    )


def apply_conflict_budget(
    record: RebaseState,
    *,
    prior: RebaseState,
    target: str,
    resolver_suppressed: bool,
    identity: ConflictIdentity = _UNKNOWN_IDENTITY,
    attempts: int = MAX_CONSECUTIVE_RESOLVER_ATTEMPTS,
    retry_deadline: float | None = None,
    clock: object = None,
    cooldown_seconds: float | None = None,
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
    carried = prior_conflict_count(prior, target, identity, attempts=attempts)
    effective_deadline = retry_deadline
    if effective_deadline is None and clock is not None and cooldown_seconds is not None:
        effective_deadline = _read_clock_seconds(clock) + float(cooldown_seconds)

    if record.fast_forwarded:
        updated = record.record_consecutive_conflicts(0, cap=attempts).set_retry_deadline(None)
        return updated.model_copy(
            update={
                "last_conflict_feature_sha": None,
                "last_conflict_target_sha": None,
                "last_conflict_paths": (),
                "last_conflict_stage_oids": (),
                "last_conflict_scope": "",
                "last_conflict_strategy_index": 0,
            }
        )
    if record.last_outcome != RebaseOutcomeKind.CONFLICT:
        deadline_value = (
            effective_deadline
            if effective_deadline is not None
            else (
                record.retry_deadline if record.retry_deadline is not None else prior.retry_deadline
            )
        )
        updated = (
            record.record_consecutive_conflicts(carried, cap=attempts)
            .set_retry_deadline(deadline_value)
            .project_conflict_strategy_from(prior)
        )
        return updated.model_copy(
            update={
                "last_conflict_feature_sha": prior.last_conflict_feature_sha,
                "last_conflict_target_sha": prior.last_conflict_target_sha,
                "last_conflict_paths": prior.last_conflict_paths,
                "last_conflict_stage_oids": prior.last_conflict_stage_oids,
                "last_conflict_scope": prior.last_conflict_scope,
                "last_conflict_strategy_index": prior.last_conflict_strategy_index,
            }
        )

    deadline_value = (
        effective_deadline
        if effective_deadline is not None
        else (record.retry_deadline if record.retry_deadline is not None else prior.retry_deadline)
    )
    updated = (
        record.record_consecutive_conflicts(min(attempts, carried + 1), cap=attempts)
        .set_retry_deadline(deadline_value)
        .project_conflict_strategy_from(prior)
    )
    update: dict[str, object] = {
        "last_conflict_feature_sha": identity.feature_sha,
        "last_conflict_target_sha": identity.target_sha,
        "last_conflict_paths": identity.conflicted_paths,
        "last_conflict_stage_oids": identity.stage_oids,
        "last_conflict_scope": identity.scope,
        "last_conflict_strategy_index": prior.conflict_strategy_index,
    }
    if resolver_suppressed:
        update["last_reason"] = (
            f"conflict resolution budget exhausted for '{target}' after "
            f"{min(carried, attempts)} unresolved attempts; escalating to the next resolution strategy"
        )
    return updated.model_copy(update=update)


_MAX_ACTIVE_CONFLICT_ATTEMPTS = 64
_ACTIVE_CONFLICT_ATTEMPTS: OrderedDict[tuple[object, ...], None] = (
    OrderedDict()  # bounded-accumulator-ok: FIFO-capped by _MAX_ACTIVE_CONFLICT_ATTEMPTS
)


def _conflict_attempt_key(identity: ConflictIdentity) -> tuple[object, ...]:
    return (
        identity.feature_sha,
        identity.target_sha,
        identity.conflicted_paths,
        identity.stage_oids,
        identity.scope,
    )


def start_conflict_attempt(identity: ConflictIdentity) -> bool:
    """Book one in-flight resolution for ``identity``; reject duplicates."""
    key = _conflict_attempt_key(identity)
    if key in _ACTIVE_CONFLICT_ATTEMPTS:
        return False
    while len(_ACTIVE_CONFLICT_ATTEMPTS) >= _MAX_ACTIVE_CONFLICT_ATTEMPTS:
        _ACTIVE_CONFLICT_ATTEMPTS.popitem(last=False)
    _ACTIVE_CONFLICT_ATTEMPTS[key] = None
    return True


def finish_conflict_attempt(identity: ConflictIdentity) -> None:
    """Release an in-flight resolution booking."""
    _ACTIVE_CONFLICT_ATTEMPTS.pop(_conflict_attempt_key(identity), None)
