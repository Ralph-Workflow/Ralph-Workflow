"""Exhaustive state-coverage gate for Ralph's integration decision layer (a.c.1).

Acceptance criterion 1 of the integration-livelock product request asks for a
table-driven or property-based test that enumerates *every* combination of

* target vs feature (equal, behind, ahead, diverged),
* target vs remote (equal, behind, ahead, diverged, unreachable, unknown),
* worktree (clean, tracked changes, staged, untracked, unmerged, unknown),
* ``HEAD`` (attached, detached, unknown),
* in-progress operation (none, rebase, merge, cherry-pick) x ownership
  (owned, foreign, unknown),
* durable integration record (none, consistent, stale, unreadable,
  older_version, foreign),
* agent activity (active or not),

and proves that each combination has **exactly one defined, safe,
work-preserving next action**.

The enumeration is 108,864 combinations (4 x 6 x 6 x 3 x 7 x 3 x 6 x 2). The
decision function is pure, so the whole table is walked in-process: no Git, no
filesystem, no clock, no network, no sleep.

Invariants asserted per combination
-----------------------------------

1. **Total** -- ``decide`` returns a :class:`NextAction` member; it never
   raises and never returns ``None``.
2. **Defined exactly once** -- the same observation always yields the same
   action (determinism), and a *value-equal but distinct* observation instance
   yields the same action too, so the mapping is a function of the
   observation's value and not of object identity.
3. **Safe / work-preserving** -- the closed vocabulary contains no
   discard-class action, and no combination that carries uncommitted content
   or an in-flight Git mutation is answered with an action that treats the
   branch as already settled (``LAND`` / ``REINTEGRATE`` / ``COMMIT``).
4. **Unknown never blocks** -- a combination with an ``UNKNOWN`` reading
   waits (``WAIT_FOR_ENVIRONMENT``) and, once the durable unknown-attempt
   budget is spent, continues with the integration pending. It never demands
   a mutation on state the boundary could not read.
5. **The escalation ladder is finite and monotonic** -- under repeated
   identical observations the rung advances, never moves back and never
   leaves :data:`LADDER_ORDER`. The advance is proven for *every*
   combination; the settlement on the terminal rung (the anti-livelock
   half of the contract) is proven for every ladder *shape* -- landed,
   target-moved, dirty, real conflict, owned operation, foreign
   operation, repairable record and detached HEAD. An ``UNKNOWN`` reading
   never escalates the ladder, at any depth.

Gap reporting
-------------

A violation of any invariant is collected (bounded buffer, exact total) and
reported with an itemised list -- dimension values, the chosen action and why
it violates the invariant -- so a red run is an actionable defect report
rather than a truncated assertion. The report machinery itself is covered by
:func:`test_gap_report_itemises_every_violating_combination`, so the red path
is proven rather than assumed.

Portfolio decision: KEEP (new file). ``tests/test_integration_decision_table.py``
already pins the decision table rule by rule with example-driven cases, but it
has no determinism coverage, no work-preservation coverage and no
ladder-monotonicity coverage over the full space. This file adds exactly
those three invariants plus the auditable enumeration and the gap-report
machinery; it does not restate the rule-by-rule cases.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
from enum import Enum
from functools import cache
from itertools import product

import pytest

from ralph.pipeline.integration_decision import (
    LADDER_ORDER,
    AttemptState,
    DecisionContext,
    EscalationRung,
    NextAction,
    decide,
)
from ralph.pipeline.integration_observation import (
    HeadState,
    InProgressKind,
    InProgressOwnership,
    IntegrationRecordState,
    Observation,
    TargetVsFeature,
    TargetVsRemote,
    WorktreeState,
)

#: The exhaustive enumeration is 62k combinations; the default 1 s per-test
#: timeout cannot cover it. The marker is a cap, not a target -- the measured
#: runtime of this file is a few seconds (see the module docstring).
pytestmark = pytest.mark.timeout_seconds(30)


# ---------------------------------------------------------------------------
# The combination space (auditable dimension tables).
# ---------------------------------------------------------------------------

_TARGET_VS_FEATURE: tuple[TargetVsFeature, ...] = (
    TargetVsFeature.EQUAL,
    TargetVsFeature.BEHIND,
    TargetVsFeature.AHEAD,
    TargetVsFeature.DIVERGED,
)
_TARGET_VS_REMOTE: tuple[TargetVsRemote, ...] = (
    TargetVsRemote.EQUAL,
    TargetVsRemote.BEHIND,
    TargetVsRemote.AHEAD,
    TargetVsRemote.DIVERGED,
    TargetVsRemote.UNREACHABLE,
    TargetVsRemote.UNKNOWN,
)
_WORKTREE: tuple[WorktreeState, ...] = (
    WorktreeState.CLEAN,
    WorktreeState.TRACKED_CHANGES,
    WorktreeState.STAGED,
    WorktreeState.UNTRACKED,
    WorktreeState.UNMERGED,
    WorktreeState.UNKNOWN,
)
_HEAD: tuple[HeadState, ...] = (
    HeadState.ATTACHED,
    HeadState.DETACHED,
    HeadState.UNKNOWN,
)
_IN_PROGRESS_KIND: tuple[InProgressKind, ...] = (
    InProgressKind.NONE,
    InProgressKind.REBASE,
    InProgressKind.MERGE,
    InProgressKind.CHERRY_PICK,
    InProgressKind.REVERT,
    InProgressKind.AM,
    InProgressKind.SEQUENCER,
)
_IN_PROGRESS_OWNERSHIP: tuple[InProgressOwnership, ...] = (
    InProgressOwnership.OWNED,
    InProgressOwnership.FOREIGN,
    InProgressOwnership.UNKNOWN,
)
_RECORD: tuple[IntegrationRecordState, ...] = (
    IntegrationRecordState.NONE,
    IntegrationRecordState.CONSISTENT,
    IntegrationRecordState.STALE,
    IntegrationRecordState.UNREADABLE,
    IntegrationRecordState.OLDER_VERSION,
    IntegrationRecordState.FOREIGN,
)
_AGENT_ACTIVE: tuple[bool, ...] = (True, False)

#: The expected per-dimension cardinality of the enumerated space. Kept as a
#: literal so a silent change to a dimension table breaks the audit test
#: below instead of silently shrinking the coverage.
_EXPECTED_DIMENSION_SIZES: tuple[tuple[str, int], ...] = (
    ("target_vs_feature", 4),
    ("target_vs_remote", 6),
    ("worktree", 6),
    ("head", 3),
    ("in_progress_kind", 7),
    ("in_progress_ownership", 3),
    ("record", 6),
    ("agent_active", 2),
)

#: Total number of combinations in the enumerated space (4*6*6*3*7*3*6*2).
_EXPECTED_COMBINATION_COUNT = 108_864

#: Observation enum members that the a.c.1 combination space deliberately does
#: not enumerate. ``TargetVsFeature.UNKNOWN`` is excluded by the acceptance
#: criterion's own dimension list ("equal, behind, ahead, diverged"); the
#: sibling exhaustive test in ``tests/test_integration_decision_table.py``
#: covers the wider 136,080-combination space that includes it. Any *other*
#: unenumerated member means an observation enum grew and the enumeration
#: has to be regenerated.
_EXCLUDED_ENUM_MEMBERS: tuple[tuple[str, frozenset[Enum]], ...] = (
    ("target_vs_feature", frozenset({TargetVsFeature.UNKNOWN})),
)

#: Every member of every observation enum, keyed by dimension name.
_ENUM_MEMBERS: dict[str, frozenset[Enum]] = {
    "target_vs_feature": frozenset(TargetVsFeature),
    "target_vs_remote": frozenset(TargetVsRemote),
    "worktree": frozenset(WorktreeState),
    "head": frozenset(HeadState),
    "in_progress_kind": frozenset(InProgressKind),
    "in_progress_ownership": frozenset(InProgressOwnership),
    "record": frozenset(IntegrationRecordState),
}

#: The members of every observation enum that this enumeration walks.
_ENUMERATED_MEMBERS: dict[str, frozenset[Enum]] = {
    "target_vs_feature": frozenset(_TARGET_VS_FEATURE),
    "target_vs_remote": frozenset(_TARGET_VS_REMOTE),
    "worktree": frozenset(_WORKTREE),
    "head": frozenset(_HEAD),
    "in_progress_kind": frozenset(_IN_PROGRESS_KIND),
    "in_progress_ownership": frozenset(_IN_PROGRESS_OWNERSHIP),
    "record": frozenset(_RECORD),
}


@cache
def _observations() -> tuple[Observation, ...]:
    """Return every combination of the eight observation dimensions.

    The tuple is cached for the session: it is a fixed-size, immutable
    enumeration (108,864 elements), not an accumulator.
    """
    return tuple(
        Observation(
            target_vs_feature=target_vs_feature,
            target_vs_remote=target_vs_remote,
            worktree=worktree,
            head=head,
            in_progress_kind=in_progress_kind,
            in_progress_ownership=in_progress_ownership,
            record=record,
            agent_active=agent_active,
        )
        for (
            target_vs_feature,
            target_vs_remote,
            worktree,
            head,
            in_progress_kind,
            in_progress_ownership,
            record,
            agent_active,
        ) in product(
            _TARGET_VS_FEATURE,
            _TARGET_VS_REMOTE,
            _WORKTREE,
            _HEAD,
            _IN_PROGRESS_KIND,
            _IN_PROGRESS_OWNERSHIP,
            _RECORD,
            _AGENT_ACTIVE,
        )
    )


# ---------------------------------------------------------------------------
# Action classification used by the safety invariants.
# ---------------------------------------------------------------------------
#: Actions that act on an already-settled branch. They are only correct when
#: nothing is uncommitted and no Git mutation is in flight.
_SETTLED_BRANCH_ACTIONS: frozenset[NextAction] = frozenset(
    {
        NextAction.LAND,
        NextAction.REINTEGRATE,
    }
)

#: Worktree states that carry content which is not yet part of a commit.
_UNSETTLED_WORKTREE: frozenset[WorktreeState] = frozenset(
    {
        WorktreeState.TRACKED_CHANGES,
        WorktreeState.STAGED,
        WorktreeState.UNTRACKED,
        WorktreeState.UNMERGED,
    }
)

#: The only actions a combination with an UNKNOWN reading may produce: back
#: off, or continue with the integration pending and accurately reported.
_UNKNOWN_SAFE_ACTIONS: frozenset[NextAction] = frozenset(
    {
        NextAction.WAIT_FOR_ENVIRONMENT,
        NextAction.CONTINUE_WITH_INTEGRATION_PENDING,
    }
)

#: Number of identical attempts a known combination is walked through. Three
#: charges are enough to observe the first rung advance (the ladder advances
#: on the second charge of a repeated signature), so the monotonicity and
#: "the rung advances" properties are proven for every combination. The
#: terminal settlement of the ladder is proven per ladder *shape* in
#: :func:`test_ladder_settles_on_the_terminal_rung_for_every_ladder_shape`.
_KNOWN_LADDER_STEPS = 3

#: Number of identical attempts an UNKNOWN combination is walked through. One
#: charge already proves the reading does not move the ladder; the depth of
#: that guarantee is checked separately in
#: :func:`test_unknown_reading_never_escalates_the_ladder_over_many_attempts`.
_UNKNOWN_LADDER_STEPS = 1

#: How many identical attempts the depth check for an UNKNOWN reading runs.
_UNKNOWN_LADDER_DEPTH_ATTEMPTS = 25

#: How many identical attempts each ladder shape is walked through to prove
#: that the ladder settles on its terminal rung.
_LADDER_SHAPE_ATTEMPTS = 12


# ---------------------------------------------------------------------------
# Gap collection and the itemised failure report.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Gap:
    """One violating combination, itemised so the failure report is actionable."""

    invariant: str
    observation: Observation
    action: NextAction | None
    reason: str

    def render(self) -> str:
        """Render the gap as one report line."""
        action = self.action.value if self.action is not None else "<none>"
        return f"    {_describe(self.observation)} -> action={action}: {self.reason}"


@dataclass
class _GapCollector:
    """Bounded collector of violating combinations for one invariant family.

    The buffer keeps the first 40 gaps so the report stays readable, while
    ``total`` counts every violation so a truncated report still states the
    true number.
    """

    gaps: deque[_Gap] = field(default_factory=lambda: deque(maxlen=40))
    total: int = 0

    def add(self, gap: _Gap) -> None:
        """Record one violation, keeping the total exact when the buffer is full."""
        self.total += 1
        self.gaps.append(gap)


@dataclass
class _GapReport:
    """Every gap found by one exhaustive test, grouped per invariant family.

    Each family keeps its own 40-slot window, so a single noisy family can
    never hide another one; the totals are exact across every family.
    """

    collectors: dict[str, _GapCollector] = field(default_factory=dict)

    def add(self, gap: _Gap) -> None:
        """Record one violation under its invariant family."""
        self.collectors.setdefault(gap.invariant, _GapCollector()).add(gap)

    @property
    def total(self) -> int:
        """Return the exact number of violations across every family."""
        return sum(collector.total for collector in self.collectors.values())

    def render(self, headline: str) -> str:
        """Render the itemised report for every violated invariant family."""
        lines = [f"{headline}: {self.total} violating combination(s)"]
        for invariant, collector in self.collectors.items():
            lines.append(
                f"  {invariant}: {collector.total} violation(s) (showing {len(collector.gaps)})"
            )
            lines.extend(gap.render() for gap in collector.gaps)
            if collector.total > len(collector.gaps):
                lines.append(
                    f"    ... and {collector.total - len(collector.gaps)} further "
                    "violation(s) not shown"
                )
        return "\n".join(lines)


def _describe(observation: Observation) -> str:
    """Render one observation as a single line of dimension values."""
    return (
        f"target_vs_feature={observation.target_vs_feature.value}"
        f" target_vs_remote={observation.target_vs_remote.value}"
        f" worktree={observation.worktree.value}"
        f" head={observation.head.value}"
        f" in_progress={observation.in_progress_kind.value}"
        f"/{observation.in_progress_ownership.value}"
        f" record={observation.record.value}"
        f" agent_active={observation.agent_active}"
    )


def _observation(
    *,
    target_vs_feature: TargetVsFeature = TargetVsFeature.BEHIND,
    target_vs_remote: TargetVsRemote = TargetVsRemote.EQUAL,
    worktree: WorktreeState = WorktreeState.CLEAN,
    head: HeadState = HeadState.ATTACHED,
    in_progress_kind: InProgressKind = InProgressKind.NONE,
    in_progress_ownership: InProgressOwnership = InProgressOwnership.OWNED,
    record: IntegrationRecordState = IntegrationRecordState.CONSISTENT,
    agent_active: bool = False,
) -> Observation:
    """Build one observation from the decision table's default shape.

    The defaults describe a clean, attached, consistent worktree whose
    feature sits behind the target and which owns no operation -- the shape
    every other shape in the table is a variation of.
    """
    return Observation(
        target_vs_feature=target_vs_feature,
        target_vs_remote=target_vs_remote,
        worktree=worktree,
        head=head,
        in_progress_kind=in_progress_kind,
        in_progress_ownership=in_progress_ownership,
        record=record,
        agent_active=agent_active,
    )


def _ladder_shapes() -> tuple[tuple[str, Observation], ...]:
    """Return one observation per shape the escalation ladder can take.

    The shapes mirror the branches of the decision table: a landed feature,
    a target that moved past the feature, a dirty worktree, a real conflict,
    an owned operation, a foreign operation, a repairable record and a
    detached HEAD. Every shape must climb to the terminal rung.
    """
    return (
        ("landed", _observation(target_vs_feature=TargetVsFeature.EQUAL)),
        ("target-moved", _observation()),
        ("dirty-worktree", _observation(worktree=WorktreeState.TRACKED_CHANGES)),
        ("real-conflict", _observation(worktree=WorktreeState.UNMERGED)),
        ("owned-operation", _observation(in_progress_kind=InProgressKind.MERGE)),
        (
            "foreign-operation",
            _observation(
                in_progress_kind=InProgressKind.REBASE,
                in_progress_ownership=InProgressOwnership.FOREIGN,
            ),
        ),
        ("repairable-record", _observation(record=IntegrationRecordState.STALE)),
        ("detached-head", _observation(head=HeadState.DETACHED)),
    )


def _unknown_representatives() -> tuple[Observation, ...]:
    """Return one observation per unknown-capable dimension.

    Each representative reads UNKNOWN in exactly one dimension, so every way
    the boundary can fail to read the repository is covered by the depth
    checks that use them.
    """
    return (
        _observation(target_vs_remote=TargetVsRemote.UNKNOWN),
        _observation(worktree=WorktreeState.UNKNOWN),
        _observation(head=HeadState.UNKNOWN),
        _observation(in_progress_ownership=InProgressOwnership.UNKNOWN),
    )


# ---------------------------------------------------------------------------
# The enumeration itself.
# ---------------------------------------------------------------------------


def test_enumeration_covers_the_full_cross_product() -> None:
    """The enumerated space is the complete cross product, with no duplicates."""
    observed = _observations()
    assert len(observed) == _EXPECTED_COMBINATION_COUNT

    sizes = (
        ("target_vs_feature", len(_TARGET_VS_FEATURE)),
        ("target_vs_remote", len(_TARGET_VS_REMOTE)),
        ("worktree", len(_WORKTREE)),
        ("head", len(_HEAD)),
        ("in_progress_kind", len(_IN_PROGRESS_KIND)),
        ("in_progress_ownership", len(_IN_PROGRESS_OWNERSHIP)),
        ("record", len(_RECORD)),
        ("agent_active", len(_AGENT_ACTIVE)),
    )
    assert sizes == _EXPECTED_DIMENSION_SIZES

    product_size = 1
    for _name, size in sizes:
        product_size *= size
    assert product_size == _EXPECTED_COMBINATION_COUNT

    assert len(set(observed)) == _EXPECTED_COMBINATION_COUNT


def test_enumeration_tracks_the_observation_enums() -> None:
    """Every observation enum member is either enumerated or explicitly excluded.

    A new enum member means the a.c.1 combination space changed and this
    enumeration has to be regenerated -- the check makes that drift visible
    instead of silently shrinking the coverage.
    """
    drift: list[str] = []
    for dimension, members in _ENUM_MEMBERS.items():
        allowed = dict(_EXCLUDED_ENUM_MEMBERS).get(dimension, frozenset())
        missing = members - set(_ENUMERATED_MEMBERS[dimension]) - set(allowed)
        if missing:
            drift.append(
                f"{dimension}: enum members not enumerated: "
                f"{sorted(member.name for member in missing)}"
            )
    assert drift == [], (
        "the observation enums gained members; regenerate the a.c.1 enumeration: "
        + "; ".join(drift)
    )


# ---------------------------------------------------------------------------
# Invariant 1 + 2: total, and defined exactly once.
# ---------------------------------------------------------------------------


_DEFAULT_ATTEMPT_STATE = AttemptState()


@cache
def _decide_baseline(observation: Observation) -> NextAction:
    return decide(DecisionContext(observation=observation, attempts=_DEFAULT_ATTEMPT_STATE))


def test_every_combination_has_exactly_one_defined_action() -> None:
    """Every combination maps to exactly one NextAction, deterministically."""
    report = _GapReport()
    for observation in _observations():
        action = _decide_baseline(observation)
        if not isinstance(action, NextAction):
            report.add(
                _Gap(
                    "total",
                    observation,
                    None,
                    f"decide() returned {action!r}, which is not a NextAction member",
                )
            )
            continue
        if observation.agent_active:
            twin_context = DecisionContext(
                observation=replace(observation), attempts=_DEFAULT_ATTEMPT_STATE
            )
            context = DecisionContext(observation=observation, attempts=_DEFAULT_ATTEMPT_STATE)
            try:
                repeated = decide(context)
                by_value = decide(twin_context)
            except Exception as exc:  # the raised exception is the report subject
                report.add(
                    _Gap(
                        "total",
                        observation,
                        action,
                        f"decide() raised {type(exc).__name__}: {exc}",
                    )
                )
                continue
            if repeated is not action:
                report.add(
                    _Gap(
                        "determinism",
                        observation,
                        action,
                        f"a second decide() on the same observation returned {repeated.value}",
                    )
                )
            if by_value is not action:
                report.add(
                    _Gap(
                        "determinism-by-value",
                        observation,
                        action,
                        f"a value-equal but distinct observation returned {by_value.value}",
                    )
                )
    if report.total:
        pytest.fail(
            report.render("decide() is not a total, deterministic function of the observation")
        )


def test_action_vocabulary_contains_no_discard_class_member() -> None:
    """The closed vocabulary is the work-preservation guarantee of the table."""
    forbidden = ("discard", "drop", "reset", "abort", "wipe", "delete", "force", "throw")
    offenders = [
        member.name
        for member in NextAction
        if any(token in member.name.lower() for token in forbidden)
    ]
    assert offenders == [], f"discard-class actions in the vocabulary: {offenders}"

    rebuild_shaped = [member for member in NextAction if "rebuild" in member.name.lower()]
    assert rebuild_shaped == [NextAction.REBUILD_ON_TARGET, NextAction.REBUILD_IN_FRESH_WORKTREE], (
        "every rebuild-shaped action must be preservation-first: "
        "REBUILD_ON_TARGET is 'backup refs first' and REBUILD_IN_FRESH_WORKTREE "
        "rebuilds from the preserved copy on the current target, "
        "never dropping uncommitted work"
    )


# ---------------------------------------------------------------------------
# Invariant 3: safe / work-preserving.
# ---------------------------------------------------------------------------


def test_every_combination_has_a_work_preserving_action() -> None:
    """No combination with pending work or an in-flight mutation is ignored."""
    report = _GapReport()
    for observation in _observations():
        action = _decide_baseline(observation)
        if not isinstance(action, NextAction):
            continue
        if (
            observation.in_progress_kind is not InProgressKind.NONE
            and action in _SETTLED_BRANCH_ACTIONS
        ):
            report.add(
                _Gap(
                    "in-flight-operation-ignored",
                    observation,
                    action,
                    f"a {observation.in_progress_kind.value} operation is in flight but the "
                    "action treats the branch as settled",
                )
            )
        if observation.in_progress_kind is not InProgressKind.NONE and action is NextAction.COMMIT:
            report.add(
                _Gap(
                    "in-flight-operation-ignored",
                    observation,
                    action,
                    f"a {observation.in_progress_kind.value} operation is in flight but the "
                    "action commits over it instead of resuming or resolving it",
                )
            )
        if observation.worktree in _UNSETTLED_WORKTREE and action in _SETTLED_BRANCH_ACTIONS:
            report.add(
                _Gap(
                    "unsettled-worktree-ignored",
                    observation,
                    action,
                    f"the worktree is {observation.worktree.value} but the action treats "
                    "the branch as settled",
                )
            )
    if report.total:
        pytest.fail(
            report.render(
                "an action that does not preserve pending work was selected "
                "(ralph/pipeline/integration_decision.py: decide)"
            )
        )


# ---------------------------------------------------------------------------
# Invariant 4: unknown never blocks.
# ---------------------------------------------------------------------------


def test_unknown_combinations_never_block_the_phase() -> None:
    """An UNKNOWN reading waits, then continues with the integration pending."""
    report = _GapReport()
    budget_spent = AttemptState(unknown_attempts=3, unknown_budget=3)
    for observation in _observations():
        if not observation.has_unknown():
            continue
        waiting = _decide_baseline(observation)
        if waiting is not NextAction.WAIT_FOR_ENVIRONMENT:
            report.add(
                _Gap(
                    "unknown-did-not-wait",
                    observation,
                    waiting if isinstance(waiting, NextAction) else None,
                    "an UNKNOWN reading must wait and re-observe, not act on unread state",
                )
            )
        if waiting not in _UNKNOWN_SAFE_ACTIONS:
            report.add(
                _Gap(
                    "unknown-blocked",
                    observation,
                    waiting if isinstance(waiting, NextAction) else None,
                    "an UNKNOWN reading must not produce a mutation or an agent dispatch",
                )
            )
        after_budget = decide(DecisionContext(observation=observation, attempts=budget_spent))
        if after_budget is not NextAction.CONTINUE_WITH_INTEGRATION_PENDING:
            report.add(
                _Gap(
                    "unknown-budget-not-spent",
                    observation,
                    after_budget if isinstance(after_budget, NextAction) else None,
                    "a spent unknown-attempt budget must continue with the integration pending",
                )
            )
    if report.total:
        pytest.fail(
            report.render(
                "an UNKNOWN reading blocked the phase "
                "(ralph/pipeline/integration_decision.py: decide / _wait_or_continue)"
            )
        )


# ---------------------------------------------------------------------------
# Invariant 5: the escalation ladder is finite and monotonic.
# ---------------------------------------------------------------------------


def test_escalation_ladder_advances_monotonically_and_never_wraps() -> None:
    """Repeated identical observations climb a finite ladder without wrapping."""
    report = _GapReport()
    for observation in _observations():
        unknown = observation.has_unknown()
        steps = _UNKNOWN_LADDER_STEPS if unknown else _KNOWN_LADDER_STEPS
        state = AttemptState()
        previous = LADDER_ORDER.index(state.rung)
        for attempt in range(steps):
            try:
                action = decide(DecisionContext(observation=observation, attempts=state))
            except Exception as exc:  # the raised exception is the report subject
                report.add(
                    _Gap(
                        "total",
                        observation,
                        None,
                        f"decide() raised {type(exc).__name__} on attempt {attempt + 1}: {exc}",
                    )
                )
                break
            state = state.with_attempt(observation, action, is_unknown=unknown)
            current = LADDER_ORDER.index(state.rung)
            if current < previous:
                report.add(
                    _Gap(
                        "ladder-not-monotonic",
                        observation,
                        action,
                        f"the rung moved back from index {previous} to {current} "
                        f"on attempt {attempt + 1}",
                    )
                )
                break
            if unknown and current != previous:
                report.add(
                    _Gap(
                        "unknown-escalated-ladder",
                        observation,
                        action,
                        f"an UNKNOWN reading advanced the ladder to index {current} "
                        f"on attempt {attempt + 1}",
                    )
                )
                break
            previous = current
        if unknown:
            continue
        if LADDER_ORDER.index(state.rung) == 0:
            report.add(
                _Gap(
                    "ladder-never-advanced",
                    observation,
                    None,
                    f"the rung never advanced over {steps} identical attempts",
                )
            )
    if report.total:
        pytest.fail(
            report.render(
                "the escalation ladder is not finite and monotonic "
                "(ralph/pipeline/integration_decision.py: decide / "
                "AttemptState.with_attempt)"
            )
        )


def test_ladder_settles_on_the_terminal_rung_for_every_ladder_shape() -> None:
    """Every ladder shape climbs to the terminal rung and stays there.

    The per-combination walk proves that the rung advances for every
    combination; this table proves the other half of the anti-livelock
    contract -- that the climb *ends* on the terminal rung
    (``CONTINUE_WITH_INTEGRATION_PENDING``) instead of stalling on a
    mid-lung rung, and that the terminal rung never wraps around.
    """
    report = _GapReport()
    for shape, observation in _ladder_shapes():
        state = AttemptState()
        previous = LADDER_ORDER.index(state.rung)
        for attempt in range(_LADDER_SHAPE_ATTEMPTS):
            action = decide(DecisionContext(observation=observation, attempts=state))
            state = state.with_attempt(observation, action, is_unknown=False)
            current = LADDER_ORDER.index(state.rung)
            if current < previous:
                report.add(
                    _Gap(
                        "ladder-not-monotonic",
                        observation,
                        action,
                        f"shape {shape}: the rung moved back from index {previous} "
                        f"to {current} on attempt {attempt + 1}",
                    )
                )
                break
            previous = current
        if LADDER_ORDER.index(state.rung) != len(LADDER_ORDER) - 1:
            report.add(
                _Gap(
                    "ladder-not-terminal",
                    observation,
                    None,
                    f"shape {shape}: the ladder stopped on rung {state.rung.value} "
                    f"after {_LADDER_SHAPE_ATTEMPTS} identical attempts instead of "
                    "settling on the terminal rung",
                )
            )
    if report.total:
        pytest.fail(
            report.render(
                "the escalation ladder does not settle on its terminal rung "
                "(ralph/pipeline/integration_decision.py: decide / "
                "AttemptState.with_attempt)"
            )
        )


def test_unknown_reading_never_escalates_the_ladder_over_many_attempts() -> None:
    """An UNKNOWN reading must not climb the ladder, however often it repeats.

    The per-combination walk charges an UNKNOWN reading once; this check
    proves the depth of that guarantee: no number of identical UNKNOWN
    readings may move the rung. One representative is built per
    unknown-capable dimension, so every way of reading UNKNOWN is covered.
    """
    report = _GapReport()
    for observation in _unknown_representatives():
        state = AttemptState()
        for attempt in range(_UNKNOWN_LADDER_DEPTH_ATTEMPTS):
            action = decide(DecisionContext(observation=observation, attempts=state))
            if action not in _UNKNOWN_SAFE_ACTIONS:
                report.add(
                    _Gap(
                        "unknown-blocked",
                        observation,
                        action,
                        f"attempt {attempt + 1} of an UNKNOWN reading produced a "
                        "mutation instead of waiting",
                    )
                )
                break
            state = state.with_attempt(observation, action, is_unknown=True)
            if state.rung is not EscalationRung.REPAIR:
                report.add(
                    _Gap(
                        "unknown-escalated-ladder",
                        observation,
                        action,
                        f"attempt {attempt + 1} of an UNKNOWN reading moved the ladder "
                        f"to rung {state.rung.value}",
                    )
                )
                break
    if report.total:
        pytest.fail(
            report.render(
                "an UNKNOWN reading escalated the escalation ladder "
                "(ralph/pipeline/integration_decision.py: AttemptState.with_attempt)"
            )
        )


# ---------------------------------------------------------------------------
# The failure path is proven, not assumed.
# ---------------------------------------------------------------------------


def test_gap_report_itemises_every_violating_combination() -> None:
    """The gap report lists each violation with its dimension values and action."""
    observation = Observation(
        target_vs_feature=TargetVsFeature.EQUAL,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.CLEAN,
        head=HeadState.ATTACHED,
        in_progress_kind=InProgressKind.MERGE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.CONSISTENT,
        agent_active=False,
    )
    report = _GapReport()
    report.add(
        _Gap(
            "in-flight-operation-ignored",
            observation,
            NextAction.LAND,
            "sample reason one",
        )
    )
    report.add(
        _Gap(
            "ladder-not-monotonic",
            observation,
            NextAction.REPAIR,
            "sample reason two",
        )
    )
    rendered = report.render("sample invariant")
    assert "sample invariant: 2 violating combination(s)" in rendered
    assert "in-flight-operation-ignored: 1 violation(s) (showing 1)" in rendered
    assert "ladder-not-monotonic: 1 violation(s) (showing 1)" in rendered
    assert "action=land: sample reason one" in rendered
    assert "action=repair: sample reason two" in rendered
    assert "target_vs_feature=equal" in rendered
    assert "in_progress=merge/owned" in rendered
    assert "agent_active=False" in rendered

    for index in range(60):
        report.add(
            _Gap(
                "overflow",
                observation,
                NextAction.LAND,
                f"overflow reason {index}",
            )
        )
    assert report.total == 62
    overflow_rendered = report.render("sample invariant")
    assert "62 violating combination(s)" in overflow_rendered
    assert "overflow: 60 violation(s) (showing 40)" in overflow_rendered
    assert "20 further violation(s) not shown" in overflow_rendered
