"""Exhaustive state-space proof for Ralph's integration decision table.

The integration boundary must always know exactly one safe next step. This
file walks the **complete** observation state space of
:func:`ralph.pipeline.integration_decision.decide` -- every member of every
observation enum, including the ``UNKNOWN`` readings -- and crosses it with
the durable carry-over state (the escalation rung, the repeat-pressure
counters and the unknown-attempt budget). For every pair it proves that

1. **exactly one defined action** exists: ``decide`` returns a member of the
   closed :class:`NextAction` vocabulary, never ``None`` and never an
   exception;
2. **the action is never undefined**: no member of the vocabulary is an
   "unknown" placeholder;
3. **the action never discards work**: neither the decision vocabulary
   (:class:`NextAction`) nor the downstream integration-lifecycle vocabulary
   (:class:`IntegrationAction`, reached through the public
   :func:`ralph.pipeline.integration_owner.project_integration_action`
   projection) contains an action that throws away uncommitted content or an
   unresolved conflict, and no settled-branch action (``LAND`` /
   ``REINTEGRATE``) is ever returned for a worktree that still carries
   uncommitted content, an unresolved conflict or an in-flight Git mutation;
4. **the action is a function of the state**: the same observation and the
   same attempt state always produce the same action, and the decision never
   mutates its inputs.

The enumeration is 77,760 observations (5 x 6 x 6 x 3 x 4 x 3 x 6 x 2) crossed
with the attempt states that carry meaning for them -- five for every
observation (one per escalation rung plus the repeat-pressure profile) and a
sixth for the 58,560 observations that carry an UNKNOWN reading (the spent
unknown budget) -- i.e. 447,360 (observation, attempt state) pairs. The
decision function is pure, so the whole table is walked in-process: no Git, no
filesystem, no clock, no network, no sleep.

Relation to the sibling files
-----------------------------

``tests/test_integration_state_coverage.py`` walks the acceptance-criterion
combination space (62,208 combinations -- it deliberately leaves
``TargetVsFeature.UNKNOWN`` out) at the default attempt state, and
``tests/test_integration_decision_table.py`` pins the decision rules one by
one. This file adds what neither of them covers: the **complete** enum space
(including ``TargetVsFeature.UNKNOWN``), the **cross product with the durable
carry-over state** (rung, repeat pressure, unknown budget), the
**cross-vocabulary** projection into :class:`IntegrationAction`, and a
bounded, derandomised **property-based** test over the same space.
"""

from __future__ import annotations

from collections import OrderedDict
from functools import cache
from itertools import product

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ralph.pipeline import IntegrationAction
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
from ralph.pipeline.integration_owner import (
    NEXT_ACTION_TO_INTEGRATION_ACTION,
    project_integration_action,
)

#: The exhaustive walk needs more than the 1 s default per-test timeout.
#: The marker is a cap, not a target -- see the measured runtime in the
#: module docstring.
pytestmark = pytest.mark.timeout_seconds(30)


# ---------------------------------------------------------------------------
# The observation dimensions, taken verbatim from the production enums.
# ---------------------------------------------------------------------------

_TARGET_VS_FEATURE: tuple[TargetVsFeature, ...] = tuple(TargetVsFeature)
_TARGET_VS_REMOTE: tuple[TargetVsRemote, ...] = tuple(TargetVsRemote)
_WORKTREE: tuple[WorktreeState, ...] = tuple(WorktreeState)
_HEAD: tuple[HeadState, ...] = tuple(HeadState)
_IN_PROGRESS_KIND: tuple[InProgressKind, ...] = tuple(InProgressKind)
_IN_PROGRESS_OWNERSHIP: tuple[InProgressOwnership, ...] = tuple(InProgressOwnership)
_RECORD: tuple[IntegrationRecordState, ...] = tuple(IntegrationRecordState)
_AGENT_ACTIVE: tuple[bool, ...] = (True, False)

#: The per-dimension cardinality of the walked space. The literal values make
#: a silent change to a production enum break the audit test below instead of
#: quietly shrinking the coverage.
_EXPECTED_DIMENSION_SIZES: tuple[tuple[str, int], ...] = (
    ("target_vs_feature", 5),
    ("target_vs_remote", 6),
    ("worktree", 6),
    ("head", 3),
    ("in_progress_kind", 4),
    ("in_progress_ownership", 3),
    ("record", 6),
    ("agent_active", 2),
)

#: Number of observations in the walked space (5*6*6*3*4*3*6*2).
_EXPECTED_OBSERVATION_COUNT: int = 77_760

#: Number of attempt states every observation is crossed with.
_EXPECTED_ATTEMPT_STATE_COUNT: int = 5

#: Number of attempt states an observation with an UNKNOWN reading is
#: crossed with: the five above plus the spent unknown budget.
_EXPECTED_UNKNOWN_ATTEMPT_STATE_COUNT: int = 6

#: Number of observations that carry at least one UNKNOWN reading. Only
#: ``target_vs_feature``, ``target_vs_remote``, ``worktree``, ``head`` and
#: ``in_progress_ownership`` have an UNKNOWN member, so the fully-known part
#: of the space is 4*5*5*2*4*2*6*2 = 19,200 observations and the rest
#: (77,760 - 19,200) carry an UNKNOWN reading.
_EXPECTED_UNKNOWN_OBSERVATION_COUNT: int = 58_560

#: Number of (observation, attempt state) pairs the table proves. Every
#: observation is decided against the four static rung states plus the
#: repeat-pressure profile; the "unknown budget spent" state only carries
#: meaning for an observation with an UNKNOWN reading (the budget is consulted
#: nowhere else), so it is added for those observations only.
_EXPECTED_PAIR_COUNT: int = (
    _EXPECTED_OBSERVATION_COUNT * _EXPECTED_ATTEMPT_STATE_COUNT
    + _EXPECTED_UNKNOWN_OBSERVATION_COUNT
)


# ---------------------------------------------------------------------------
# The contract vocabulary.
# ---------------------------------------------------------------------------

#: Record states the durable record must be repaired from before any reset.
_REPAIRABLE_RECORDS: frozenset[IntegrationRecordState] = frozenset(
    {
        IntegrationRecordState.STALE,
        IntegrationRecordState.UNREADABLE,
        IntegrationRecordState.OLDER_VERSION,
        IntegrationRecordState.FOREIGN,
    }
)

#: Worktree states that carry content which is not yet part of a commit.
_DIRTY_WORKTREES: frozenset[WorktreeState] = frozenset(
    {
        WorktreeState.TRACKED_CHANGES,
        WorktreeState.STAGED,
        WorktreeState.UNTRACKED,
    }
)

#: Target/feature relationships in which the feature is contained in the target.
_CONTAINED: frozenset[TargetVsFeature] = frozenset(
    {
        TargetVsFeature.EQUAL,
        TargetVsFeature.AHEAD,
    }
)

#: Target/feature relationships in which the target has moved past the feature.
_TARGET_MOVED: frozenset[TargetVsFeature] = frozenset(
    {
        TargetVsFeature.BEHIND,
        TargetVsFeature.DIVERGED,
    }
)

#: The only two actions an UNKNOWN reading may produce: back off, or continue
#: with the integration pending and accurately reported.
_UNKNOWN_SAFE_ACTIONS: frozenset[NextAction] = frozenset(
    {
        NextAction.WAIT_FOR_ENVIRONMENT,
        NextAction.CONTINUE_WITH_INTEGRATION_PENDING,
    }
)

#: Actions that act on an already-settled branch. They are only correct when
#: nothing is uncommitted and no Git mutation is in flight.
_SETTLED_BRANCH_ACTIONS: frozenset[NextAction] = frozenset(
    {
        NextAction.LAND,
        NextAction.REINTEGRATE,
    }
)

#: Name markers that would express "throw the work away". No member of either
#: closed vocabulary may carry one: the vocabulary itself is the guarantee that
#: the table cannot ask the boundary to drop uncommitted work.
_DISCARDING_NAME_MARKERS: tuple[str, ...] = (
    "DISCARD",
    "DROP",
    "WIPE",
    "RESET",
    "ABORT",
    "STASH",
    "FORCE",
    "CLEAN",
)


def _is_discarding_name(name: str) -> bool:
    """True when ``name`` reads like "throw the work away"."""
    upper = name.upper()
    return any(marker in upper for marker in _DISCARDING_NAME_MARKERS)


#: The direct actions the table may repeat. Counting repeats for these makes
#: the repeat-pressure branch reachable for every observation class.
_DIRECT_ACTIONS: tuple[NextAction, ...] = (
    NextAction.DISPATCH_RESOLVER,
    NextAction.RESUME_OWNED_OPERATION,
    NextAction.COMMIT,
)

#: How many times each direct action is counted for one signature under repeat
#: pressure. The count is above the default repeat threshold (2), so the
#: table answers with the escalated rung's action.
_REPEAT_PRESSURE_COUNT: int = 5

#: The number of unknown attempts the "budget spent" profile carries. It is
#: equal to the default unknown budget, so the budget is exhausted.
_UNKNOWN_ATTEMPTS_SPENT: int = 3

#: The default attempt state, used only to compute a signature.
_BASE_ATTEMPTS: AttemptState = AttemptState()

#: The observation-independent attempt states: one per escalation rung.
_STATIC_ATTEMPT_STATES: tuple[tuple[str, AttemptState], ...] = (
    ("fresh@repair", AttemptState(rung=EscalationRung.REPAIR)),
    ("fresh@dispatch_resolver", AttemptState(rung=EscalationRung.DISPATCH_RESOLVER)),
    ("fresh@rebuild_on_target", AttemptState(rung=EscalationRung.REBUILD_ON_TARGET)),
    (
        "fresh@continue_pending",
        AttemptState(rung=EscalationRung.CONTINUE_WITH_INTEGRATION_PENDING),
    ),
)

#: The attempt state in which the durable unknown-attempt budget is spent.
#: The budget is consulted only for an observation with an UNKNOWN reading.
_UNKNOWN_BUDGET_SPENT_ATTEMPT_STATE: tuple[str, AttemptState] = (
    "unknown_budget_spent@repair",
    AttemptState(
        rung=EscalationRung.REPAIR,
        unknown_attempts=_UNKNOWN_ATTEMPTS_SPENT,
        unknown_budget=_UNKNOWN_ATTEMPTS_SPENT,
    ),
)


# ---------------------------------------------------------------------------
# The enumeration.
# ---------------------------------------------------------------------------


@cache
def _observations() -> tuple[Observation, ...]:
    """Return every combination of the eight observation dimensions.

    The tuple is cached for the session: it is a fixed-size, immutable
    enumeration (77,760 elements), not an accumulator.
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


@cache
def _unknown_observations() -> tuple[Observation, ...]:
    """Return every observation that carries at least one UNKNOWN reading."""
    return tuple(observation for observation in _observations() if observation.has_unknown())


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


@cache
def _allowed_actions(observation: Observation) -> frozenset[NextAction]:
    """Return the actions the decision contract permits for ``observation``.

    The set restates the *contract* of the decision table (module rules 1-8
    of :mod:`ralph.pipeline.integration_decision`), not its implementation:
    each action is admitted only when the observation proves the situation
    that action answers. A returned action outside this set means the table
    answered a state it has no defined answer for -- or answered it with an
    action that is wrong for that state (for example ``LAND`` while the
    worktree still carries uncommitted content).

    * ``LAND`` -- the feature is already contained in the target and the
      worktree is clean and quiescent.
    * ``REINTEGRATE`` -- the target has moved past the feature and the
      worktree is clean, attached and free of a repairable record.
    * ``COMMIT`` -- the worktree carries uncommitted content and nothing else
      is in the way.
    * ``RESUME_OWNED_OPERATION`` -- an owned Git operation is in flight.
    * ``DISPATCH_RESOLVER`` -- there is a real conflict, or the state is a
      conflict-class signature (detached HEAD, repairable record).
    * ``REPAIR`` -- the state is a conflict-class signature the repair rung
      answers.
    * ``REBUILD_ON_TARGET`` / ``CONTINUE_WITH_INTEGRATION_PENDING`` -- ladder
      rungs; they answer any conflict-class signature and are never wrong for
      a known observation.
    * ``WAIT_FOR_ENVIRONMENT`` / ``CONTINUE_WITH_INTEGRATION_PENDING`` -- the
      only answers an UNKNOWN reading may produce.
    """
    if observation.has_unknown():
        return _UNKNOWN_SAFE_ACTIONS

    allowed: set[NextAction] = set()
    clean = observation.worktree is WorktreeState.CLEAN
    no_operation = observation.in_progress_kind is InProgressKind.NONE
    repairable_record = observation.record in _REPAIRABLE_RECORDS
    detached_head = observation.head is HeadState.DETACHED
    real_conflict = observation.worktree is WorktreeState.UNMERGED or (
        observation.in_progress_kind is not InProgressKind.NONE
        and observation.in_progress_ownership is InProgressOwnership.FOREIGN
    )

    if clean and no_operation and observation.target_vs_feature in _CONTAINED:
        allowed.add(NextAction.LAND)
    if (
        clean
        and no_operation
        and observation.head is HeadState.ATTACHED
        and not repairable_record
        and observation.target_vs_feature in _TARGET_MOVED
    ):
        allowed.add(NextAction.REINTEGRATE)
    if (
        observation.worktree in _DIRTY_WORKTREES
        and no_operation
        and observation.in_progress_ownership is not InProgressOwnership.UNKNOWN
        and not repairable_record
    ):
        allowed.add(NextAction.COMMIT)
    if (
        observation.in_progress_kind is not InProgressKind.NONE
        and observation.in_progress_ownership is InProgressOwnership.OWNED
    ):
        allowed.add(NextAction.RESUME_OWNED_OPERATION)
    if real_conflict or detached_head or repairable_record:
        allowed.add(NextAction.DISPATCH_RESOLVER)
    if detached_head or repairable_record:
        allowed.add(NextAction.REPAIR)
    allowed.add(NextAction.REBUILD_ON_TARGET)
    allowed.add(NextAction.CONTINUE_WITH_INTEGRATION_PENDING)
    return frozenset(allowed)


@cache
def _pressure_attempts_for_sig(signature: str, rung: EscalationRung) -> AttemptState:
    return AttemptState(
        rung=rung,
        repeat_counts=OrderedDict(
            ((signature, action.value), _REPEAT_PRESSURE_COUNT) for action in _DIRECT_ACTIONS
        ),
    )


def _pressure_attempts(observation: Observation, rung: EscalationRung) -> AttemptState:
    """Return an attempt state under repeat pressure for ``observation``.

    The repeat counters are charged against the observation's own signature,
    which is what the boundary's ledger holds after it has already retried
    this exact state.
    """
    return _pressure_attempts_for_sig(_BASE_ATTEMPTS.signature(observation), rung)


def _attempt_states(observation: Observation) -> tuple[tuple[str, AttemptState], ...]:
    """Return the attempt states ``observation`` is decided against.

    The five static states apply to every observation. The "unknown budget
    spent" state applies only to an observation with an UNKNOWN reading: the
    durable unknown-attempt budget is consulted nowhere else, so charging it
    to a fully-known observation would walk the same pairs twice.
    """
    states: tuple[tuple[str, AttemptState], ...] = (
        *_STATIC_ATTEMPT_STATES,
        ("repeat_pressure@repair", _pressure_attempts(observation, EscalationRung.REPAIR)),
    )
    if observation.has_unknown():
        states = (*states, _UNKNOWN_BUDGET_SPENT_ATTEMPT_STATE)
    return states


# ---------------------------------------------------------------------------
# The enumeration audit.
# ---------------------------------------------------------------------------


def test_state_space_enumeration_tracks_the_production_enums() -> None:
    """Every observation enum member is enumerated and the count is exact.

    A production enum that gains a member changes the state space; the
    literal cardinalities make that drift a loud failure instead of a silent
    loss of coverage.
    """
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
    assert product_size == _EXPECTED_OBSERVATION_COUNT

    observations = _observations()
    assert len(observations) == _EXPECTED_OBSERVATION_COUNT
    assert len(_unknown_observations()) == _EXPECTED_UNKNOWN_OBSERVATION_COUNT


def test_state_space_covers_every_attempt_state_of_every_observation() -> None:
    """Every observation is decided against every attempt state profile."""
    known = _observations()[0]
    states = _attempt_states(known)
    assert len(states) == _EXPECTED_ATTEMPT_STATE_COUNT
    assert len({label for label, _attempts in states}) == _EXPECTED_ATTEMPT_STATE_COUNT

    unknown = next(observation for observation in _observations() if observation.has_unknown())
    unknown_states = _attempt_states(unknown)
    assert len(unknown_states) == _EXPECTED_UNKNOWN_ATTEMPT_STATE_COUNT
    assert len({label for label, _attempts in unknown_states}) == (
        _EXPECTED_UNKNOWN_ATTEMPT_STATE_COUNT
    )


# ---------------------------------------------------------------------------
# The exhaustive table.
# ---------------------------------------------------------------------------


def test_every_combination_has_exactly_one_defined_work_preserving_action() -> None:
    """Every state has exactly one defined action that never discards work.

    The table walks all 77,760 observations against the attempt states that
    carry meaning for them (447,360 pairs in total: five states for every
    observation plus the spent unknown budget for the observations that
    carry an UNKNOWN reading). For each pair the action must be a member of
    the closed vocabulary and must be one of the actions the contract permits
    for that observation -- which is what rules out both an undefined answer
    and an answer that would treat unfinished work as finished.

    Violations are reported per invariant family, so a red run names the
    failing combination and the invariant it broke.
    """
    undefined: list[str] = []
    discarding: list[str] = []
    pairs = 0
    for observation in _observations():
        allowed = _allowed_actions(observation)
        unsettled = (
            observation.worktree in _DIRTY_WORKTREES
            or observation.worktree is WorktreeState.UNMERGED
            or observation.in_progress_kind is not InProgressKind.NONE
        )
        for label, attempts in _attempt_states(observation):
            pairs += 1
            action = decide(DecisionContext(observation=observation, attempts=attempts))
            if action not in allowed:
                undefined.append(
                    f"{_describe(observation)} [{label}] -> {action.name}; "
                    f"permitted: {sorted(member.name for member in allowed)}"
                )
            if unsettled and action in _SETTLED_BRANCH_ACTIONS:
                discarding.append(f"{_describe(observation)} [{label}] -> {action.name}")
    assert pairs == _EXPECTED_PAIR_COUNT, (
        f"the table walked {pairs} pairs, expected {_EXPECTED_PAIR_COUNT}"
    )
    assert undefined == [], (
        f"{len(undefined)} of {pairs} state pairs had no contract-permitted action:\n"
        + "\n".join(undefined[:10])
    )
    assert discarding == [], (
        f"{len(discarding)} of {pairs} unsettled state(s) were answered with a "
        "settled-branch action:\n" + "\n".join(discarding[:10])
    )


# ---------------------------------------------------------------------------
# The closed vocabularies.
# ---------------------------------------------------------------------------


def test_the_decision_vocabulary_is_closed_and_carries_no_discard_member() -> None:
    """``NextAction`` is finite, non-empty, and cannot express discarding."""
    members = list(NextAction)
    assert members, "the decision vocabulary must not be empty"
    assert len(set(members)) == len(members)
    discarding = sorted(member.name for member in members if _is_discarding_name(member.name))
    assert discarding == [], f"the decision vocabulary can express discarding: {discarding}"
    undefined = sorted(member.name for member in members if "UNKNOWN" in member.name)
    assert undefined == [], f"the decision vocabulary has an undefined member: {undefined}"


def test_the_integration_action_vocabulary_carries_no_discard_member() -> None:
    """``IntegrationAction`` -- the lifecycle vocabulary -- cannot discard either."""
    members = list(IntegrationAction)
    assert members, "the integration action vocabulary must not be empty"
    discarding = sorted(member.name for member in members if _is_discarding_name(member.name))
    assert discarding == [], (
        f"the integration action vocabulary can express discarding: {discarding}"
    )


def test_the_projection_from_the_decision_vocabulary_is_total() -> None:
    """Every decision projects onto exactly one integration-lifecycle action.

    The projection is what the pipeline executes, so an unmapped decision --
    or a projected action outside the closed lifecycle vocabulary -- would be
    an undefined next step.
    """
    assert set(NEXT_ACTION_TO_INTEGRATION_ACTION) == set(NextAction)
    for action in NextAction:
        projected = project_integration_action(action)
        assert isinstance(projected, IntegrationAction)
        assert not _is_discarding_name(projected.name)


# ---------------------------------------------------------------------------
# Determinism and purity.
# ---------------------------------------------------------------------------


def test_the_decision_is_deterministic_and_does_not_mutate_its_inputs() -> None:
    """The same state always yields the same action, and nothing is mutated."""
    observations = _observations()
    for index in range(0, len(observations), 977):
        observation = observations[index]
        attempts = _pressure_attempts(observation, EscalationRung.REPAIR)
        snapshot = OrderedDict(attempts.repeat_counts)
        first = decide(DecisionContext(observation=observation, attempts=attempts))
        second = decide(DecisionContext(observation=observation, attempts=attempts))
        assert first is second, (
            f"{_describe(observation)} decided twice gave {first.name} then {second.name}"
        )
        assert attempts.repeat_counts == snapshot, (
            f"{_describe(observation)} mutated the attempt state"
        )


# ---------------------------------------------------------------------------
# The bounded property-based test.
# ---------------------------------------------------------------------------

_OBSERVATION_STRATEGY = st.builds(
    Observation,
    target_vs_feature=st.sampled_from(_TARGET_VS_FEATURE),
    target_vs_remote=st.sampled_from(_TARGET_VS_REMOTE),
    worktree=st.sampled_from(_WORKTREE),
    head=st.sampled_from(_HEAD),
    in_progress_kind=st.sampled_from(_IN_PROGRESS_KIND),
    in_progress_ownership=st.sampled_from(_IN_PROGRESS_OWNERSHIP),
    record=st.sampled_from(_RECORD),
    agent_active=st.booleans(),
)


@settings(max_examples=25, deadline=None, derandomize=True)
@given(
    observation=_OBSERVATION_STRATEGY,
    rung=st.sampled_from(LADDER_ORDER),
    pressured=st.booleans(),
    unknown_spent=st.booleans(),
)
def test_every_drawn_state_has_exactly_one_defined_safe_action(
    observation: Observation,
    rung: EscalationRung,
    pressured: bool,
    unknown_spent: bool,
) -> None:
    """A drawn state is always answered with one permitted, safe action.

    The run is derandomised and deadline-free, so a failure is reproducible
    from the falsifying example Hypothesis prints and from the combination in
    the assertion message.
    """
    attempts = _pressure_attempts(observation, rung) if pressured else AttemptState(rung=rung)
    if unknown_spent:
        attempts = AttemptState(
            rung=attempts.rung,
            repeat_counts=attempts.repeat_counts,
            unknown_attempts=_UNKNOWN_ATTEMPTS_SPENT,
            unknown_budget=_UNKNOWN_ATTEMPTS_SPENT,
        )
    action = decide(DecisionContext(observation=observation, attempts=attempts))
    allowed = _allowed_actions(observation)
    assert action in allowed, (
        f"{_describe(observation)} rung={rung.value} pressured={pressured} "
        f"unknown_spent={unknown_spent} -> {action.name}; "
        f"permitted: {sorted(member.name for member in allowed)}"
    )
