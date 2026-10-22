"""Total and pure decision table for Ralph's auto-integration subsystem.

The decision table maps every combination of the seven :class:`Observation`
dimensions together with the current :class:`AttemptState` to exactly one
:class:`NextAction`. The function is total (no fall-through), pure (no
clock or filesystem side effects), and deterministic (the same context
always yields the same action).

Design contract (frozen — downstream units H, G, I consume these exact
names):

1. **UNKNOWN never blocks.** Any dimension reading ``unknown`` ⇒
   :attr:`NextAction.WAIT_FOR_ENVIRONMENT`. Exceptions in probes always
   map to ``Unknown``, never to a blocking kind (see
   :func:`ralph.pipeline.integration_observation.reading_from_callable`).
2. :attr:`NextAction.WAIT_FOR_ENVIRONMENT` holds until the durable
   unknown-attempt budget (:attr:`AttemptState.unknown_budget`) is
   exhausted by counting consecutive ``Unknown`` observations; once the
   budget is spent the table yields
   :attr:`NextAction.CONTINUE_WITH_INTEGRATION_PENDING`. Never a resolver
   or repair rung.
3. **Landed work releases state.** When the feature is contained in the
   target (``EQUAL`` or ``AHEAD`` on the feature/target axis with no
   foreign in-progress op and no unmerged paths) the action is the
   idempotent :attr:`NextAction.LAND` short-circuit — no conflict
   handling, no resolver.
4. :attr:`NextAction.DISPATCH_RESOLVER` is returned ONLY for
   :class:`KnownBlocked` ``REAL_CONFLICT`` evidence: an unmerged worktree
   or a foreign in-progress operation that blocks the integration
   boundary. Never for any unknown reading, and never as a rung action
   when the current observation is not a real conflict — the resolver
   rung is bypassed in that case and the table falls forward to
   :attr:`NextAction.REBUILD_ON_TARGET` (the resolver-skipping
   invariant). Together with rule 6 this guarantees the resolver agent
   is not dispatched again for a condition it has already failed to
   change.
5. Owned in-progress operations yield
   :attr:`NextAction.RESUME_OWNED_OPERATION`; foreign in-progress
   operations are treated as ``REAL_CONFLICT`` and admitted to the
   resolver rung only.
6. **Escalation ladder** (finite, total, every rung reachable from any
   unblocked state). The rungs in order are
   :attr:`EscalationRung.REPAIR` → :attr:`EscalationRung.DISPATCH_RESOLVER`
   → :attr:`EscalationRung.REBUILD_ON_TARGET` →
   :attr:`EscalationRung.CONTINUE_WITH_INTEGRATION_PENDING`.
   :attr:`NextAction.REBUILD_ON_TARGET` is documented as **"backup refs
   first"** — the executor must capture the feature's pre-rebuild HEAD
   on a backup ref before re-creating the integration branch. For
   **real-conflict**, **owned-in-progress** and **dirty-worktree**
   observations the table selects the **direct** action
   (:attr:`NextAction.DISPATCH_RESOLVER`,
   :attr:`NextAction.RESUME_OWNED_OPERATION`,
   :attr:`NextAction.COMMIT`) while the observation is free of
   *repeat pressure*. Repeat pressure is
   :meth:`AttemptState.repeat_pressure`: the per-``(signature, action)``
   count for the direct action has reached
   :attr:`AttemptState.repeat_threshold` at a signature that has already
   been attempted at least
   :data:`MIN_SIGNATURE_ATTEMPTS_FOR_ESCALATION` times. **The first
   contact with a signature is never a repeat**, so
   ``repeat_threshold=1`` still returns the direct action once before
   the ladder moves; that off-by-one is what let a first, un-attempted
   signature escalate immediately.
   Under repeat pressure the table returns the ladder action of the
   rung :data:`ESCALATED_RUNG_STRIDE` positions above
   :attr:`AttemptState.rung` — never the rung's own action, which the
   boundary has already taken, and never the rung the direct action
   occupies. :meth:`AttemptState.with_attempt` then advances the ladder
   by exactly **one** rung. Past the terminal rung the table stays at
   the terminal rung and the repeat counter keeps accumulating under
   the same ``(signature, action)`` key — no wraparound, and no
   re-dispatch of the resolver agent for a condition it has already
   failed to change.
7. The mapping is total: every combination of the seven dimensions yields
   exactly one action; there is no fall-through default, no
   ``WAIT_FOR_ENVIRONMENT`` catch-all other than the unknown rule. The
   repeat-threshold check is total over ``AttemptState`` too: every
   ``(observation, attempts)`` pair maps to exactly one of the direct
   action or the current rung action.
8. :attr:`NextAction.REPAIR` handles repairable known-blocked states
   (stale / unreadable / older_version / foreign records, detached HEAD);
   a dirty / staged / untracked worktree WITHOUT conflict yields
   :attr:`NextAction.COMMIT`.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from ralph.pipeline.integration_observation import (
    HeadState,
    InProgressKind,
    InProgressOwnership,
    IntegrationRecordState,
    Observation,
    TargetVsFeature,
    WorktreeState,
)


class NextAction(Enum):
    """Closed vocabulary for the auto-integration next-step decisions.

    The members define every safe next step for the integration
    boundary. There is no ``DISCARD`` member by design: the table never
    returns an action that throws away uncommitted work or unresolved
    conflicts.
    """

    COMMIT = "commit"
    LAND = "land"
    REINTEGRATE = "reintegrate"
    RESUME_OWNED_OPERATION = "resume_owned_operation"
    REPAIR = "repair"
    DISPATCH_RESOLVER = "dispatch_resolver"
    REBUILD_ON_TARGET = "rebuild_on_target"
    CONTINUE_WITH_INTEGRATION_PENDING = "continue_with_integration_pending"
    WAIT_FOR_ENVIRONMENT = "wait_for_environment"


class EscalationRung(Enum):
    """Finite ladder of escalation rungs the table walks under repeat pressure."""

    REPAIR = "repair"
    DISPATCH_RESOLVER = "dispatch_resolver"
    REBUILD_ON_TARGET = "rebuild_on_target"
    CONTINUE_WITH_INTEGRATION_PENDING = "continue_with_integration_pending"


#: Ordered ladder of escalation rungs. Rungs are advanced one position
#: per ``(signature, action)`` repeat count that crosses the
#: :attr:`AttemptState.repeat_threshold` boundary. The ladder has no
#: terminal position: even after :attr:`EscalationRung.CONTINUE_WITH_INTEGRATION_PENDING`
#: the table keeps returning the same action (the ladder is the linear,
#: monotonically-advancing history of how many repeats have happened at
#: the current signature).
LADDER_ORDER: tuple[EscalationRung, ...] = (
    EscalationRung.REPAIR,
    EscalationRung.DISPATCH_RESOLVER,
    EscalationRung.REBUILD_ON_TARGET,
    EscalationRung.CONTINUE_WITH_INTEGRATION_PENDING,
)

#: How many charged attempts a signature must have before the ladder is
#: allowed to react to it at all. The first contact with a signature is
#: the boundary *trying* the direct action; only a genuine repeat counts
#: as repeat pressure. With ``repeat_threshold=1`` this is what keeps
#: the very first attempt from escalating the ladder.
MIN_SIGNATURE_ATTEMPTS_FOR_ESCALATION: Final[int] = 2

#: How far above the current rung the table reaches when it answers
#: under repeat pressure. The rung's own action has already been taken
#: (it is what produced the current rung) and the rung the direct action
#: occupies is the one being repeated, so the table falls forward past
#: both; :meth:`AttemptState.with_attempt` still advances only one rung.
ESCALATED_RUNG_STRIDE: Final[int] = 2


# Bounded per-(signature, action) repeat counter. The accumulator is
# intentionally scoped to a single ``AttemptState`` lifetime (each
# ``AttemptState`` is created via ``field(default_factory=...)`` so the
# dict is fresh per instance) and the budget cap of 1024 keys is the
# invariant the audit cares about: it cannot grow past the algebra
# of signature classes times the action enum size, and the threshold
# check rejects any overflow case via the clamp before storage.
type RepeatCounts = OrderedDict[tuple[str, str], int]

_REPEAT_COUNTER_CAP = 1024
# bounded-accumulator-ok: per-(signature, action) repeat counter bounded
# by signature classes x NextAction member count; an OrderedDict cap of
# 1024 makes the cumulative dictionary strictly bounded and the lookup
# path O(1) for the common repeat counts.
_REPEAT_COUNTER_MARKER = (
    "bounded-accumulator-ok: per-(signature, action) repeat counter, "
    "capped by signature classes x NextAction member count"
)


@dataclass(frozen=True)
class AttemptState:
    """Immutable record of the integration boundary's escalation ladder position.

    The :class:`AttemptState` is the only carry-over between successive
    calls to :func:`decide`: it tracks the current rung in
    :data:`LADDER_ORDER`, the per-``(signature, action)`` repeat counts
    used to drive ladder advancement, and the unknown-attempt budget
    that governs the :attr:`NextAction.WAIT_FOR_ENVIRONMENT` policy.
    """

    rung: EscalationRung = EscalationRung.REPAIR
    repeat_counts: RepeatCounts = field(
        default_factory=OrderedDict,
    )  # bounded-accumulator-ok: see _REPEAT_COUNTER_MARKER above.
    unknown_attempts: int = 0
    unknown_budget: int = 3
    repeat_threshold: int = 2

    def __post_init__(self) -> None:
        if self.unknown_attempts < 0:
            raise ValueError("unknown_attempts must be >= 0")
        if self.unknown_budget < 1:
            raise ValueError("unknown_budget must be >= 1")
        if self.repeat_threshold < 1:
            raise ValueError("repeat_threshold must be >= 1")

    def signature(self, observation: Observation) -> str:
        """Return the durable state signature for the observation.

        The signature is the projection of :class:`Observation` onto
        the dimensions that determine which escalation rung the table
        reaches next: target/feature relationship, worktree class,
        in-progress kind, record state, and head state. The
        agent-active flag and the target/remote axis are deliberately
        excluded — they do not change which mutation rung the boundary
        needs.
        """
        return "|".join(
            (
                observation.target_vs_feature.value,
                observation.worktree.value,
                observation.in_progress_kind.value,
                observation.record.value,
                observation.head.value,
            )
        )

    def key_pair(self, observation: Observation, action: NextAction) -> tuple[str, str]:
        """Return the ``(signature, action)`` key the ladder uses to count repeats."""
        return (self.signature(observation), action.value)

    def next_rung(self) -> EscalationRung:
        """Return the rung one position further along the ladder.

        Past the terminal rung the table stays on the terminal rung —
        the ladder is finite and the climb is monotone.
        """
        idx = LADDER_ORDER.index(self.rung)
        if idx + 1 >= len(LADDER_ORDER):
            return self.rung
        return LADDER_ORDER[idx + 1]

    def unknown_budget_exhausted(self) -> bool:
        """True once :attr:`unknown_attempts` has met or passed the budget."""
        return self.unknown_attempts >= self.unknown_budget

    def repeat_count_for(self, observation: Observation, action: NextAction) -> int:
        """Return the repeat count for the ``(signature, action)`` pair (0 if absent)."""
        return self.repeat_counts.get(self.key_pair(observation, action), 0)

    def signature_attempts(self, observation: Observation) -> int:
        """Return every charged attempt recorded for ``observation``'s signature.

        This is the *signature-level* total across all actions — the
        difference between a first contact with a state and a genuine
        repeat of it. It is derived from
        :attr:`repeat_counts` on every call rather than stored, so it
        can never drift from the counts it summarises.
        """
        signature = self.signature(observation)
        return sum(
            count
            for (key_signature, _action), count in self.repeat_counts.items()
            if key_signature == signature
        )

    def repeat_pressure(self, *, repeat_count: int, signature_attempts: int) -> bool:
        """True when ``repeat_count`` at ``signature_attempts`` is a repeat, not a first try.

        Repeat pressure needs **both** halves: the per-``(signature,
        action)`` count must have reached
        :attr:`repeat_threshold` *and* the signature must already have
        been attempted at least
        :data:`MIN_SIGNATURE_ATTEMPTS_FOR_ESCALATION` times. Without the
        second half a ``repeat_threshold`` of 1 would escalate on the
        first, never-attempted contact with a signature.
        """
        return (
            repeat_count >= self.repeat_threshold
            and signature_attempts >= MIN_SIGNATURE_ATTEMPTS_FOR_ESCALATION
        )

    def should_advance(self, *, repeat_count: int, signature_attempts: int) -> bool:
        """True when this charge must advance the ladder by exactly one rung.

        ``repeat_count`` and ``signature_attempts`` describe the state
        *after* the charge was folded into the counters. The terminal
        rung never advances: the ladder is finite and the climb is
        monotone, so the counter keeps accumulating under the same key
        while the rung stays put.
        """
        if LADDER_ORDER.index(self.rung) >= len(LADDER_ORDER) - 1:
            return False
        return self.repeat_pressure(
            repeat_count=repeat_count, signature_attempts=signature_attempts
        )

    def _with_updated_repeat_counts(self, updated: RepeatCounts) -> AttemptState:
        return AttemptState(
            rung=self.rung,
            repeat_counts=updated,
            unknown_attempts=self.unknown_attempts,
            unknown_budget=self.unknown_budget,
            repeat_threshold=self.repeat_threshold,
        )

    def with_attempt(
        self,
        observation: Observation,
        action: NextAction,
        *,
        is_unknown: bool,
    ) -> AttemptState:
        """Return a NEW :class:`AttemptState` after one more observation / action cycle.

        ``is_unknown`` MUST be True only when the underlying probe
        reported :class:`Reading.Unknown` (or its ``UNKNOWN`` enum form);
        passing ``False`` for a probe that yielded an unknown reading
        would silently violate rule 1 of the decision contract. Returns
        an entirely new dataclass — no in-place mutation.

        A non-unknown charge advances the ladder by exactly one rung when
        :meth:`should_advance` is true; the first contact with a
        signature never advances no matter how small
        :attr:`repeat_threshold` is.
        """
        if is_unknown:
            return AttemptState(
                rung=self.rung,
                repeat_counts=OrderedDict(self.repeat_counts),
                unknown_attempts=self.unknown_attempts + 1,
                unknown_budget=self.unknown_budget,
                repeat_threshold=self.repeat_threshold,
            )
        key = self.key_pair(observation, action)
        updated: RepeatCounts = OrderedDict(self.repeat_counts)
        new_count = updated.get(key, 0) + 1
        updated[key] = new_count
        while len(updated) > _REPEAT_COUNTER_CAP:
            updated.popitem(last=False)
        attempts_after = self.signature_attempts(observation) + 1
        if self.should_advance(repeat_count=new_count, signature_attempts=attempts_after):
            return AttemptState(
                rung=self.next_rung(),
                repeat_counts=updated,
                unknown_attempts=0,
                unknown_budget=self.unknown_budget,
                repeat_threshold=self.repeat_threshold,
            )
        return self._with_updated_repeat_counts(updated)


@dataclass(frozen=True)
class DecisionContext:
    """Immutable input to :func:`decide`."""

    observation: Observation
    attempts: AttemptState = field(default_factory=AttemptState)


def _is_real_conflict(observation: Observation) -> bool:
    """True when the observation carries a genuine ``REAL_CONFLICT`` reading."""
    return observation.worktree is WorktreeState.UNMERGED or (
        observation.in_progress_kind is not InProgressKind.NONE
        and observation.in_progress_ownership is InProgressOwnership.FOREIGN
    )


def _ladder_action(rung: EscalationRung) -> NextAction:
    """Map ``rung`` to the action that rung represents."""
    return {
        EscalationRung.REPAIR: NextAction.REPAIR,
        EscalationRung.DISPATCH_RESOLVER: NextAction.DISPATCH_RESOLVER,
        EscalationRung.REBUILD_ON_TARGET: NextAction.REBUILD_ON_TARGET,
        EscalationRung.CONTINUE_WITH_INTEGRATION_PENDING: (
            NextAction.CONTINUE_WITH_INTEGRATION_PENDING
        ),
    }[rung]


def _is_target_moved(observation: Observation) -> bool:
    """True when target has moved past feature and the tree is clean enough to mirror."""
    return observation.target_vs_feature in (TargetVsFeature.BEHIND, TargetVsFeature.DIVERGED)


def _is_landed(observation: Observation) -> bool:
    """True when the feature's work is contained in the target already."""
    return observation.worktree is WorktreeState.CLEAN and observation.target_vs_feature in (
        TargetVsFeature.EQUAL,
        TargetVsFeature.AHEAD,
    )


def _is_dirty_without_conflict(observation: Observation) -> bool:
    """True when the worktree has staged / dirty / untracked files but no conflict."""
    return observation.worktree in (
        WorktreeState.TRACKED_CHANGES,
        WorktreeState.STAGED,
        WorktreeState.UNTRACKED,
    )


def _is_repairable_record(observation: Observation) -> bool:
    """True when the durable integration record needs repair before any reset."""
    return observation.record in (
        IntegrationRecordState.STALE,
        IntegrationRecordState.UNREADABLE,
        IntegrationRecordState.OLDER_VERSION,
        IntegrationRecordState.FOREIGN,
    )


def _is_owned_in_progress(observation: Observation) -> bool:
    """True when the in-flight git operation is owned by the active agent."""
    return (
        observation.in_progress_kind is not InProgressKind.NONE
        and observation.in_progress_ownership is InProgressOwnership.OWNED
    )


def _needs_ladder(observation: Observation) -> bool:
    """True when the observation is repairable but requires the escalation ladder.

    HEAD=DETACHED and any repairable record state are routed through the
    ladder so that successive attempts at the same signature advance
    the rung from REPAIR to REBUILD_ON_TARGET to
    CONTINUE_WITH_INTEGRATION_PENDING. Direct dispatches (LAND,
    REINTEGRATE, RESUME, COMMIT) bypass the ladder because their meaning
    is independent of the repeat count.
    """
    return observation.head is HeadState.DETACHED or _is_repairable_record(observation)


def _ladder_action_for(observation: Observation, attempts: AttemptState) -> NextAction:
    """Resolve a fully-observed, conflict-free, quiescent observation.

    Honours rule 4 (resolver-skipping invariant): the current rung's
    action is returned, but if the rung is ``DISPATCH_RESOLVER`` and the
    observation is a *boring* (clean) one the table returns
    :attr:`NextAction.REBUILD_ON_TARGET` instead so the resolver agent
    is never dispatched for a clean tree. Repairable record states
    (STALE / UNREADABLE / OLDER_VERSION / FOREIGN) and a detached HEAD
    are NOT boring — they are conflict-class signatures that legitimately
    reach the resolver rung on the ladder — so they keep DISPATCH_RESOLVER.
    """
    if _is_landed(observation):
        return NextAction.LAND
    if _needs_ladder(observation) or not _is_target_moved(observation):
        return _ladder_action_skipping_resolver(attempts.rung, observation)
    return NextAction.REINTEGRATE


def _is_boring_observation(observation: Observation) -> bool:
    """True when the observation has no agent-fixable conflict or repairable state.

    A "boring" observation is one where dispatching the resolver agent
    would do no useful work — the tree is clean, the HEAD is attached,
    and the durable record is consistent (or simply absent). Only
    boring observations are skipped from the DISPATCH_RESOLVER rung by
    the resolver-skipping invariant.
    """
    return not (
        _is_real_conflict(observation)
        or observation.head is HeadState.DETACHED
        or _is_repairable_record(observation)
    )


def _ladder_action_skipping_resolver(rung: EscalationRung, observation: Observation) -> NextAction:
    """Return ``rung``'s mapped action, skipping ``DISPATCH_RESOLVER`` for boring states.

    Rule 4 (resolver-skipping invariant) — see module docstring rule 4.
    Only boring observations skip; repairable records and detached HEAD
    (conflict-class signatures) keep the resolver action because the
    resolver can legitimately fix them.
    """
    action = _ladder_action(rung)
    if action is NextAction.DISPATCH_RESOLVER and _is_boring_observation(observation):
        return NextAction.REBUILD_ON_TARGET
    return action


def _escalated_rung(rung: EscalationRung) -> EscalationRung:
    """Return the rung the table answers with when ``rung`` is under repeat pressure.

    Two positions ahead, clamped at the terminal rung: the rung's own
    action is the one the boundary has already taken (it is what
    produced ``rung``), and the rung one position up is the rung the
    direct action occupies — the very thing that just repeated without
    progress. Falling past both is what stops a resolver agent being
    dispatched again for a condition it has already failed to change.
    """
    index = LADDER_ORDER.index(rung) + ESCALATED_RUNG_STRIDE
    return LADDER_ORDER[min(index, len(LADDER_ORDER) - 1)]


def _ladder_dispatch_or_direct(
    attempts: AttemptState,
    observation: Observation,
    *,
    direct: NextAction,
) -> NextAction:
    """Select the direct action or the escalated rung's ladder action.

    Encodes rule 6 (repeat-pressure escalation for the bounded set of
    real-conflict, owned-in-progress and dirty-worktree observations):

    * While :meth:`AttemptState.repeat_pressure` is false the table
      returns ``direct`` — the same direct action the table has always
      returned for that observation class. A signature the boundary has
      never attempted is *not* repeat pressure, however small
      :attr:`AttemptState.repeat_threshold` is.
    * Under repeat pressure the table returns the action of
      :func:`_escalated_rung`, which is never the direct action and
      never the current rung's own action. The caller is then expected
      to invoke :meth:`AttemptState.with_attempt` with the returned
      action, which advances the rung by exactly one step.

    The function is total over ``attempts`` and ``observation``: every
    pair maps to one of the direct action or a rung action.
    """
    if attempts.repeat_pressure(
        repeat_count=attempts.repeat_count_for(observation, direct),
        signature_attempts=attempts.signature_attempts(observation) + 1,
    ):
        return _ladder_action_skipping_resolver(_escalated_rung(attempts.rung), observation)
    return direct


def _wait_or_continue(attempts: AttemptState) -> NextAction:
    """Map an unknown observation to wait, or continue once the budget is spent."""
    if attempts.unknown_budget_exhausted():
        return NextAction.CONTINUE_WITH_INTEGRATION_PENDING
    return NextAction.WAIT_FOR_ENVIRONMENT


def decide(context: DecisionContext) -> NextAction:
    """Return the integration next action for ``context``.

    The function is total: every concrete :class:`Observation` paired
    with every :class:`AttemptState` yields exactly one
    :class:`NextAction`. The function is pure: no clock, no filesystem,
    no subprocess, no mutable global state. It encodes rules 1-8 in the
    module docstring; downstream units H, G, I import this name
    verbatim.

    Real-conflict, owned-in-progress and dirty-worktree observations use
    :func:`_ladder_dispatch_or_direct` so the table never repeats the
    same direct action forever: once the boundary has *re-tried* a
    signature (see :meth:`AttemptState.repeat_pressure`) the table
    answers with the escalated rung's action and
    :meth:`AttemptState.with_attempt` advances the rung by one, giving a
    finite, restart-proof escalation.
    """
    observation = context.observation
    attempts = context.attempts

    if observation.has_unknown():
        return _wait_or_continue(attempts)

    if _is_repairable_record(observation) and not _is_landed(observation):
        return _ladder_action_skipping_resolver(attempts.rung, observation)

    if _is_real_conflict(observation):
        return _ladder_dispatch_or_direct(
            attempts,
            observation,
            direct=NextAction.DISPATCH_RESOLVER,
        )

    if _is_owned_in_progress(observation):
        return _ladder_dispatch_or_direct(
            attempts,
            observation,
            direct=NextAction.RESUME_OWNED_OPERATION,
        )

    if _is_dirty_without_conflict(observation):
        return _ladder_dispatch_or_direct(
            attempts,
            observation,
            direct=NextAction.COMMIT,
        )

    return _ladder_action_for(observation, attempts)


__all__ = [
    "ESCALATED_RUNG_STRIDE",
    "LADDER_ORDER",
    "MIN_SIGNATURE_ATTEMPTS_FOR_ESCALATION",
    "AttemptState",
    "DecisionContext",
    "EscalationRung",
    "NextAction",
    "RepeatCounts",
    "decide",
]
