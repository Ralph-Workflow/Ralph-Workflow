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

pytestmark = pytest.mark.timeout_seconds(30)


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

_EXPECTED_COMBINATION_COUNT = 108_864

_EXCLUDED_ENUM_MEMBERS: tuple[tuple[str, frozenset[Enum]], ...] = (
    ("target_vs_feature", frozenset({TargetVsFeature.UNKNOWN})),
)

_ENUM_MEMBERS: dict[str, frozenset[Enum]] = {
    "target_vs_feature": frozenset(TargetVsFeature),
    "target_vs_remote": frozenset(TargetVsRemote),
    "worktree": frozenset(WorktreeState),
    "head": frozenset(HeadState),
    "in_progress_kind": frozenset(InProgressKind),
    "in_progress_ownership": frozenset(InProgressOwnership),
    "record": frozenset(IntegrationRecordState),
}

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


_SETTLED_BRANCH_ACTIONS: frozenset[NextAction] = frozenset(
    {
        NextAction.LAND,
        NextAction.REINTEGRATE,
    }
)

_UNSETTLED_WORKTREE: frozenset[WorktreeState] = frozenset(
    {
        WorktreeState.TRACKED_CHANGES,
        WorktreeState.STAGED,
        WorktreeState.UNTRACKED,
        WorktreeState.UNMERGED,
    }
)

_UNKNOWN_SAFE_ACTIONS: frozenset[NextAction] = frozenset(
    {
        NextAction.WAIT_FOR_ENVIRONMENT,
        NextAction.CONTINUE_WITH_INTEGRATION_PENDING,
    }
)

_KNOWN_LADDER_STEPS = 3

_UNKNOWN_LADDER_STEPS = 1

_UNKNOWN_LADDER_DEPTH_ATTEMPTS = 25

_LADDER_SHAPE_ATTEMPTS = 12


@dataclass(frozen=True)
class _Gap:
    invariant: str
    observation: Observation
    action: NextAction | None
    reason: str

    def render(self) -> str:
        action = self.action.value if self.action is not None else "<none>"
        return f"    {_describe(self.observation)} -> action={action}: {self.reason}"


@dataclass
class _GapCollector:
    gaps: deque[_Gap] = field(default_factory=lambda: deque(maxlen=40))
    total: int = 0

    def add(self, gap: _Gap) -> None:
        self.total += 1
        self.gaps.append(gap)


@dataclass
class _GapReport:
    collectors: dict[str, _GapCollector] = field(default_factory=dict)

    def add(self, gap: _Gap) -> None:
        self.collectors.setdefault(gap.invariant, _GapCollector()).add(gap)

    @property
    def total(self) -> int:
        return sum(collector.total for collector in self.collectors.values())

    def render(self, headline: str) -> str:
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
    return (
        _observation(target_vs_remote=TargetVsRemote.UNKNOWN),
        _observation(worktree=WorktreeState.UNKNOWN),
        _observation(head=HeadState.UNKNOWN),
        _observation(in_progress_ownership=InProgressOwnership.UNKNOWN),
    )


def test_enumeration_covers_the_full_cross_product() -> None:
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


_DEFAULT_ATTEMPT_STATE = AttemptState()


@cache
def _decide_baseline(observation: Observation) -> NextAction:
    return decide(DecisionContext(observation=observation, attempts=_DEFAULT_ATTEMPT_STATE))


def test_every_combination_has_exactly_one_defined_action() -> None:
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
            except Exception as exc:
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


def test_every_combination_has_a_work_preserving_action() -> None:
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


def test_unknown_combinations_never_block_the_phase() -> None:
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


def test_escalation_ladder_advances_monotonically_and_never_wraps() -> None:
    report = _GapReport()
    for observation in _observations():
        if observation.agent_active:
            continue
        unknown = observation.has_unknown()
        steps = _UNKNOWN_LADDER_STEPS if unknown else _KNOWN_LADDER_STEPS
        state = AttemptState()
        previous = LADDER_ORDER.index(state.rung)
        for attempt in range(steps):
            try:
                action = decide(DecisionContext(observation=observation, attempts=state))
            except Exception as exc:
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


def test_gap_report_itemises_every_violating_combination() -> None:
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
