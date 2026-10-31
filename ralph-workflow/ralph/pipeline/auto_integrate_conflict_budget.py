"""Bookkeeping for repeatedly unresolvable integration conflicts.

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

This module owns the REPOSITORY-SIDE bookkeeping and nothing else:

* :data:`MAX_TRACKED_CONFLICT_ATTEMPTS` -- how many CONSECUTIVE
  unresolved conflicts against the same target are STORED on the
  record. It bounds the stored counter only: the conflict identity
  and the operator escalation that reads it. It never selects,
  suppresses or demotes an agent.
* :class:`ConflictIdentity` -- the observation pair that decides
  whether the next seam is looking at the SAME unresolved conflict.
* :func:`resolver_allowed` -- the predicate the integration seam
  consults before handing the resolver down. Agent eligibility is
  decided by :class:`~ralph.recovery.agent_failure_ledger.AgentFailureLedger`
  and by NOTHING else: this module holds no second, count-based
  selector.
* :func:`apply_conflict_budget` -- carries the counter forward onto the
  outcome, resets it on any successful land, and rewrites the recorded
  reason to name the exhausted budget and the blocked target when the
  agent was suppressed.

Agent selection is a single-owner decision. The stored
``consecutive_conflicts`` counter describes the REPOSITORY (how many
times this one conflict has been observed unresolved), never the health
of an agent; the health of an agent lives in the failure ledger, which
owns cooldown, demotion and eligibility. A resolver with a high stored
conflict count is therefore still eligible whenever the ledger says so,
and a resolver the ledger has benched is never eligible however low the
stored count is.

Which ledger speaks for the ACTIVE conflict-resolution session is
answered in exactly one place --
:func:`~ralph.pipeline.conflict_resolution.chain_candidates.session_ledger`
-- so this seam cannot gate the resolver against a different ledger
than the resolver records its own failures in.

The counter is CONSECUTIVE by design: any successful land resets it to
zero, so a transient conflict can never permanently consume the
bookkeeping. Run startup passes a fresh :class:`RebaseState`, which
deliberately grants one clean observation per run.

The counter is also scoped to ONE conflict, not merely to one target
branch NAME. The mainline this run integrates onto is shared: other
agents move its tip continuously, and the developer keeps adding
feature commits between seams. A counter keyed only on ``last_target``
would therefore describe a conflict that no longer exists -- new
feature work against ``main`` would inherit the recorded history of an
older, unrelated conflict and be escalated with it.
:class:`ConflictIdentity` closes that: the count is carried forward
only while the feature tip AND the target tip are both unchanged, which
is exactly the situation in which the recorded observation still
describes the conflict on disk.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline.auto_integrate_backoff import MAX_RETRY_DEADLINE_HORIZON_SECONDS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ralph.pipeline.rebase_state import RebaseState
    from ralph.recovery.agent_failure_ledger import AgentFailureLedger

#: Upper bound on the STORED consecutive-conflict counter: how many
#: CONSECUTIVE unresolved conflicts against the same target are kept on
#: the record. The counter describes the REPOSITORY -- it scopes the
#: conflict identity and feeds the operator escalation -- and it is NOT
#: an agent-selection input: a resolver is eligible whenever the
#: failure ledger says so, whatever this count holds.
MAX_TRACKED_CONFLICT_ATTEMPTS = 64

__all__ = [
    "MAX_TRACKED_CONFLICT_ATTEMPTS",
    "ConflictIdentity",
    "apply_conflict_budget",
    "finish_conflict_attempt",
    "prior_conflict_count",
    "resolve_failure_ledger",
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

    The count describes the REPOSITORY: how many times THIS conflict has
    been observed unresolved. It is bookkeeping for the conflict identity
    and for the operator escalation -- never an agent-selection input.

    The count only applies to the SAME conflict, which takes two
    checks:

    * the SAME target -- an integration that switches mainline (a
      reconfigured ``auto_integrate_target``, or a detected default
      that changed) is a different conflict; and
    * the SAME endpoints -- a new feature commit, or a mainline tip
      another agent has since moved, is also a different conflict even
      though the branch name did not change.

    Either difference starts with a fresh count.
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


def resolve_failure_ledger(
    ledger: AgentFailureLedger | None,
) -> AgentFailureLedger | None:
    """Resolve the failure ledger that owns resolver eligibility.

    The injected ledger wins. Otherwise the ledger of the ACTIVE
    conflict-resolution session is used: that is the ledger the resolver
    itself records its failures in, so the seam and the resolver agree
    on which candidate is benched. The session's ledger is resolved
    through
    :func:`~ralph.pipeline.conflict_resolution.chain_candidates.session_ledger`
    -- the ONE resolver of "which ledger speaks for agent health" --
    so this seam can never gate the resolver against a different ledger
    than the resolver records its failures in.

    ``None`` means no ledger could be reached. The caller then has no
    authority to suppress a resolver, and :func:`resolver_allowed` keeps
    the resolver eligible -- see its docstring.
    """
    if ledger is not None:
        return ledger
    try:
        from ralph.pipeline.conflict_resolution.rebase_loop import (
            active_rebase_resolution_session,
        )

        session = active_rebase_resolution_session()
    except Exception:
        return None
    if session is None:
        return None
    from ralph.pipeline.conflict_resolution.chain_candidates import session_ledger

    return session_ledger(session)


def _is_same_conflict(
    state: RebaseState,
    target: str,
    identity: ConflictIdentity,
) -> bool:
    """Whether ``state`` recorded the conflict ``identity`` describes."""
    return (
        state.last_target == target
        and identity.matches(state)
        and state.last_conflict_strategy_index == state.conflict_strategy_index
    )


def resolver_allowed(
    state: RebaseState,
    target: str,
    identity: ConflictIdentity = _UNKNOWN_IDENTITY,
    *,
    ledger: AgentFailureLedger | None,
    candidates: Sequence[str] = (),
    phase: str = "rebase_conflict_resolution",
) -> bool:
    """Whether this attempt may invoke the dev-agent conflict resolver.

    Agent eligibility is owned by
    :class:`~ralph.recovery.agent_failure_ledger.AgentFailureLedger` and
    by NOTHING else. ``ledger`` is a REQUIRED keyword argument: there is
    no second, count-based selector behind it, and the stored
    ``consecutive_conflicts`` counter never selects, suppresses or demotes
    an agent. A resolver whose ledger entry is available is eligible even
    when the recorded conflict count is high; a resolver the ledger has
    benched is refused even when the count is zero.

    ``ledger`` is ``None`` when no ledger could be reached. The ledger
    owns every SUPPRESSION decision, and a ledger that cannot be reached
    has recorded no agent failure, so nothing suppresses the resolver:
    the resolver stays eligible and the conflict is recorded as an
    unresolved conflict. That is NOT a second opinion -- no other
    counter, streak or cap is consulted.

    The one non-ledger gate is REPOSITORY state, not agent health: when
    the record says THIS conflict already exhausted the durable
    conflict-strategy ladder (``resolution_exhausted``) the resolver is
    refused and the run escalates to the operator. A conflict whose
    identity differs is a different conflict and starts over.

    Args:
        state: The record the seam is standing on.
        target: The integration target branch.
        identity: The observed conflict identity for this attempt.
        ledger: The failure ledger that owns resolver eligibility.
        candidates: The resolver agents this attempt could invoke.
        phase: The ledger phase the candidates belong to.

    Returns:
        ``True`` when this attempt may invoke the conflict resolver.
    """
    if _is_same_conflict(state, target, identity) and state.resolution_exhausted:
        return False

    if ledger is None:
        return True

    return _ledger_candidates_available(ledger, phase, candidates)


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
    attempts: int = MAX_TRACKED_CONFLICT_ATTEMPTS,
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
      neither consumes nor forgives the recorded history.

    ``attempts`` is the cap on the STORED counter (see
    :data:`MAX_TRACKED_CONFLICT_ATTEMPTS`). It bounds what the record
    remembers about the repository; it is not an agent-selection input
    and it never decides whether a resolver may run.

    When ``resolver_suppressed`` is True and the outcome is still a
    conflict, the recorded reason is rewritten to name the exhausted
    budget and the blocked target, which is what turns an apparent
    stall into an actionable operator message.
    """
    carried = prior_conflict_count(prior, target, identity, attempts=attempts)
    effective_deadline = retry_deadline
    if effective_deadline is None and clock is not None and cooldown_seconds is not None:
        # Never arm a monotonic deadline past the horizon readers accept
        # (``retry_deadline_is_live``): beyond it a deadline is read as a
        # stale reading from a previous boot and expires.
        cooldown = min(float(cooldown_seconds), MAX_RETRY_DEADLINE_HORIZON_SECONDS)
        effective_deadline = _read_clock_seconds(clock) + cooldown

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
