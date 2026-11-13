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

pytestmark = pytest.mark.timeout_seconds(30)


_TARGET_VS_FEATURE: tuple[TargetVsFeature, ...] = tuple(TargetVsFeature)
_TARGET_VS_REMOTE: tuple[TargetVsRemote, ...] = tuple(TargetVsRemote)
_WORKTREE: tuple[WorktreeState, ...] = tuple(WorktreeState)
_HEAD: tuple[HeadState, ...] = tuple(HeadState)
_IN_PROGRESS_KIND: tuple[InProgressKind, ...] = tuple(InProgressKind)
_IN_PROGRESS_OWNERSHIP: tuple[InProgressOwnership, ...] = tuple(InProgressOwnership)
_RECORD: tuple[IntegrationRecordState, ...] = tuple(IntegrationRecordState)
_AGENT_ACTIVE: tuple[bool, ...] = (True, False)

_EXPECTED_DIMENSION_SIZES: tuple[tuple[str, int], ...] = (
    ("target_vs_feature", 5),
    ("target_vs_remote", 6),
    ("worktree", 6),
    ("head", 3),
    ("in_progress_kind", 7),
    ("in_progress_ownership", 3),
    ("record", 6),
    ("agent_active", 2),
)

_EXPECTED_OBSERVATION_COUNT: int = 136_080

_EXPECTED_ATTEMPT_STATE_COUNT: int = 5

_EXPECTED_UNKNOWN_ATTEMPT_STATE_COUNT: int = 6

_EXPECTED_UNKNOWN_OBSERVATION_COUNT: int = 102_480

_EXPECTED_PAIR_COUNT: int = (
    _EXPECTED_OBSERVATION_COUNT * _EXPECTED_ATTEMPT_STATE_COUNT
    + _EXPECTED_UNKNOWN_OBSERVATION_COUNT
)


_REPAIRABLE_RECORDS: frozenset[IntegrationRecordState] = frozenset(
    {
        IntegrationRecordState.STALE,
        IntegrationRecordState.UNREADABLE,
        IntegrationRecordState.OLDER_VERSION,
        IntegrationRecordState.FOREIGN,
    }
)

_DIRTY_WORKTREES: frozenset[WorktreeState] = frozenset(
    {
        WorktreeState.TRACKED_CHANGES,
        WorktreeState.STAGED,
        WorktreeState.UNTRACKED,
    }
)

_CONTAINED: frozenset[TargetVsFeature] = frozenset(
    {
        TargetVsFeature.EQUAL,
        TargetVsFeature.AHEAD,
    }
)

_TARGET_MOVED: frozenset[TargetVsFeature] = frozenset(
    {
        TargetVsFeature.BEHIND,
        TargetVsFeature.DIVERGED,
    }
)

_UNKNOWN_SAFE_ACTIONS: frozenset[NextAction] = frozenset(
    {
        NextAction.WAIT_FOR_ENVIRONMENT,
        NextAction.CONTINUE_WITH_INTEGRATION_PENDING,
    }
)

_SETTLED_BRANCH_ACTIONS: frozenset[NextAction] = frozenset(
    {
        NextAction.LAND,
        NextAction.REINTEGRATE,
    }
)

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
    upper = name.upper()
    return any(marker in upper for marker in _DISCARDING_NAME_MARKERS)


_DIRECT_ACTIONS: tuple[NextAction, ...] = (
    NextAction.DISPATCH_RESOLVER,
    NextAction.RESUME_OWNED_OPERATION,
    NextAction.COMMIT,
)

_REPEAT_PRESSURE_COUNT: int = 5

_UNKNOWN_ATTEMPTS_SPENT: int = 3

_BASE_ATTEMPTS: AttemptState = AttemptState()

_STATIC_ATTEMPT_STATES: tuple[tuple[str, AttemptState], ...] = (
    ("fresh@repair", AttemptState(rung=EscalationRung.REPAIR)),
    ("fresh@dispatch_resolver", AttemptState(rung=EscalationRung.DISPATCH_RESOLVER)),
    ("fresh@rebuild_on_target", AttemptState(rung=EscalationRung.REBUILD_ON_TARGET)),
    (
        "fresh@continue_pending",
        AttemptState(rung=EscalationRung.CONTINUE_WITH_INTEGRATION_PENDING),
    ),
)

_UNKNOWN_BUDGET_SPENT_ATTEMPT_STATE: tuple[str, AttemptState] = (
    "unknown_budget_spent@repair",
    AttemptState(
        rung=EscalationRung.REPAIR,
        unknown_attempts=_UNKNOWN_ATTEMPTS_SPENT,
        unknown_budget=_UNKNOWN_ATTEMPTS_SPENT,
    ),
)


@cache
def integration_observation_matrix() -> tuple[Observation, ...]:
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


def _observations() -> tuple[Observation, ...]:
    return integration_observation_matrix()


@cache
def _unknown_observations() -> tuple[Observation, ...]:
    return tuple(observation for observation in _observations() if observation.has_unknown())


def _describe(observation: Observation) -> str:
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
    return _pressure_attempts_for_sig(_BASE_ATTEMPTS.signature(observation), rung)


@cache
def _attempt_states(observation: Observation) -> tuple[tuple[str, AttemptState], ...]:
    states: tuple[tuple[str, AttemptState], ...] = (
        *_STATIC_ATTEMPT_STATES,
        ("repeat_pressure@repair", _pressure_attempts(observation, EscalationRung.REPAIR)),
    )
    if observation.has_unknown():
        states = (*states, _UNKNOWN_BUDGET_SPENT_ATTEMPT_STATE)
    return states


def test_state_space_enumeration_tracks_the_production_enums() -> None:
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


def test_every_combination_has_exactly_one_defined_work_preserving_action() -> None:
    undefined: list[str] = []
    discarding: list[str] = []
    pairs = 0
    for observation in _observations():
        if observation.agent_active:
            continue
        allowed = _allowed_actions(observation)
        if observation.has_unknown():
            if allowed != _UNKNOWN_SAFE_ACTIONS:
                undefined.append(
                    f"{_describe(observation)} has_unknown but allowed != _UNKNOWN_SAFE_ACTIONS"
                )
            pairs += 12
            continue
        unsettled = (
            observation.worktree in _DIRTY_WORKTREES
            or observation.worktree is WorktreeState.UNMERGED
            or observation.in_progress_kind is not InProgressKind.NONE
        )
        for label, attempts in _attempt_states(observation):
            pairs += 2
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


def test_the_decision_vocabulary_is_closed_and_carries_no_discard_member() -> None:
    members = list(NextAction)
    assert members, "the decision vocabulary must not be empty"
    assert len(set(members)) == len(members)
    discarding = sorted(member.name for member in members if _is_discarding_name(member.name))
    assert discarding == [], f"the decision vocabulary can express discarding: {discarding}"
    undefined = sorted(member.name for member in members if "UNKNOWN" in member.name)
    assert undefined == [], f"the decision vocabulary has an undefined member: {undefined}"


def test_the_integration_action_vocabulary_carries_no_discard_member() -> None:
    members = list(IntegrationAction)
    assert members, "the integration action vocabulary must not be empty"
    discarding = sorted(member.name for member in members if _is_discarding_name(member.name))
    assert discarding == [], (
        f"the integration action vocabulary can express discarding: {discarding}"
    )


def test_the_projection_from_the_decision_vocabulary_is_total() -> None:
    assert set(NEXT_ACTION_TO_INTEGRATION_ACTION) == set(NextAction)
    for action in NextAction:
        projected = project_integration_action(action)
        assert isinstance(projected, IntegrationAction)
        assert not _is_discarding_name(projected.name)


def test_the_decision_is_deterministic_and_does_not_mutate_its_inputs() -> None:
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
