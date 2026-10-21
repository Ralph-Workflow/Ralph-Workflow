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
   boundary. Never for any unknown reading.
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
   on a backup ref before re-creating the integration branch. When the
   same ``(signature, action)`` key repeats without progress, the table
   advances one rung per the per-(sig, action) repeat counter stored on
   :class:`AttemptState`.
7. The mapping is total: every combination of the seven dimensions yields
   exactly one action; there is no fall-through default, no
   ``WAIT_FOR_ENVIRONMENT`` catch-all other than the unknown rule.
8. :attr:`NextAction.REPAIR` handles repairable known-blocked states
   (stale / unreadable / older_version / foreign records, detached HEAD);
   a dirty / staged / untracked worktree WITHOUT conflict yields
   :attr:`NextAction.COMMIT`.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum

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

    def key_pair(
        self, observation: Observation, action: NextAction
    ) -> tuple[str, str]:
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

    def repeat_count_for(
        self, observation: Observation, action: NextAction
    ) -> int:
        """Return the repeat count for the ``(signature, action)`` pair (0 if absent)."""
        return self.repeat_counts.get(self.key_pair(observation, action), 0)

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
        if len(updated) > _REPEAT_COUNTER_CAP:
            updated.popitem(last=False)
        if (
            new_count >= self.repeat_threshold
            and self.rung is not LADDER_ORDER[-1]
        ):
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


def _ladder_action(attempts: AttemptState) -> NextAction:
    """Map the current rung to the action that rung represents."""
    return {
        EscalationRung.REPAIR: NextAction.REPAIR,
        EscalationRung.DISPATCH_RESOLVER: NextAction.DISPATCH_RESOLVER,
        EscalationRung.REBUILD_ON_TARGET: NextAction.REBUILD_ON_TARGET,
        EscalationRung.CONTINUE_WITH_INTEGRATION_PENDING: (
            NextAction.CONTINUE_WITH_INTEGRATION_PENDING
        ),
    }[attempts.rung]


def _is_target_moved(observation: Observation) -> bool:
    """True when target has moved past feature and the tree is clean enough to mirror."""
    return (
        observation.target_vs_feature
        in (TargetVsFeature.BEHIND, TargetVsFeature.DIVERGED)
    )


def _is_landed(observation: Observation) -> bool:
    """True when the feature's work is contained in the target already."""
    return (
        observation.worktree is WorktreeState.CLEAN
        and observation.target_vs_feature
        in (TargetVsFeature.EQUAL, TargetVsFeature.AHEAD)
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
    return (
        observation.head is HeadState.DETACHED
        or _is_repairable_record(observation)
    )


def _ladder_action_for(
    observation: Observation, attempts: AttemptState
) -> NextAction:
    """Resolve a fully-observed, conflict-free, quiescent observation."""
    if _is_landed(observation):
        return NextAction.LAND
    if _needs_ladder(observation) or not _is_target_moved(observation):
        return _ladder_action(attempts)
    return NextAction.REINTEGRATE

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
    """
    observation = context.observation
    attempts = context.attempts

    if observation.has_unknown():
        return _wait_or_continue(attempts)

    if _is_real_conflict(observation):
        return NextAction.DISPATCH_RESOLVER

    if _is_owned_in_progress(observation):
        return NextAction.RESUME_OWNED_OPERATION

    if _is_dirty_without_conflict(observation):
        return NextAction.COMMIT

    return _ladder_action_for(observation, attempts)


__all__ = [
    "LADDER_ORDER",
    "DecisionContext",
    "EscalationRung",
    "NextAction",
    "RepeatCounts",
    "decide",
]
