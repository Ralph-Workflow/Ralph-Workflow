from __future__ import annotations

import pytest

from ralph.pipeline import choose_integration_strategy as exported_choose_integration_strategy
from ralph.pipeline.integration_strategy import (
    STRATEGY_ORDER,
    STRATEGY_TRANSITIONS,
    IntegrationPolicy,
    IntegrationRelation,
    IntegrationStrategy,
    IntegrationWorkShape,
    ObservedIntegration,
    StrategyTransitionGuard,
    choose_integration_strategy,
)

_SHAPES: dict[str, IntegrationWorkShape] = {
    "few_linear_commits": IntegrationWorkShape(commit_count=2),
    "many_linear_commits": IntegrationWorkShape(commit_count=100, many_commits=True),
    "merge_commits": IntegrationWorkShape(commit_count=3, contains_merge_commits=True),
    "published_history": IntegrationWorkShape(
        commit_count=2,
        history_already_published=True,
    ),
    "expected_conflicts": IntegrationWorkShape(
        commit_count=2,
        conflicts_expected=True,
    ),
    "seen_conflicts": IntegrationWorkShape(
        commit_count=2,
        conflicts_seen=True,
    ),
    "hot_conflict": IntegrationWorkShape(
        commit_count=20,
        hot_conflict_repetition=3,
    ),
}

_POLICIES: dict[str, IntegrationPolicy] = {
    "permissive": IntegrationPolicy(),
    "rebase_only": IntegrationPolicy(allow_merge=False, allow_direct_integration=False),
    "merge_only": IntegrationPolicy(
        allow_nothing_to_do=False,
        allow_fast_forward=False,
        allow_rebase=False,
    ),
    "direct_only": IntegrationPolicy(
        allow_rebase=False,
        allow_merge=False,
    ),
    "terminal_only": IntegrationPolicy(
        allow_nothing_to_do=False,
        allow_fast_forward=False,
        allow_rebase=False,
        allow_merge=False,
        allow_direct_integration=False,
    ),
}

_RELATIONS: tuple[IntegrationRelation, ...] = (
    IntegrationRelation.EQUAL,
    IntegrationRelation.AHEAD,
    IntegrationRelation.BEHIND,
    IntegrationRelation.DIVERGED,
    IntegrationRelation.UNRELATED,
)


def test_pipeline_package_exports_the_strategy_decision_function() -> None:
    assert exported_choose_integration_strategy is choose_integration_strategy


def _rebase_is_safe(shape: IntegrationWorkShape) -> bool:
    return not (
        shape.many_commits
        or shape.contains_merge_commits
        or shape.history_already_published
        or shape.conflicts_expected
        or shape.conflicts_seen
        or shape.hot_conflict_repetition > 0
    )


def _expected_candidates(relation: IntegrationRelation) -> tuple[IntegrationStrategy, ...]:
    if relation is IntegrationRelation.EQUAL:
        return (
            IntegrationStrategy.NOTHING_TO_DO,
            IntegrationStrategy.COMPLETE_IN_PLACE,
        )
    if relation is IntegrationRelation.AHEAD:
        return (
            IntegrationStrategy.FAST_FORWARD,
            IntegrationStrategy.MERGE,
            IntegrationStrategy.COMPLETE_IN_PLACE,
        )
    if relation is IntegrationRelation.UNRELATED:
        return (
            IntegrationStrategy.MERGE,
            IntegrationStrategy.COMPLETE_IN_PLACE,
        )
    return (
        IntegrationStrategy.REBASE,
        IntegrationStrategy.MERGE,
        IntegrationStrategy.COMPLETE_IN_PLACE,
    )


def _expected_strategy(
    relation: IntegrationRelation,
    shape: IntegrationWorkShape,
    policy: IntegrationPolicy,
) -> IntegrationStrategy:
    merge_allowed = policy.allow_merge or policy.allow_direct_integration
    if relation is IntegrationRelation.EQUAL:
        return (
            IntegrationStrategy.NOTHING_TO_DO
            if policy.allow_nothing_to_do
            else IntegrationStrategy.COMPLETE_IN_PLACE
        )
    if relation is IntegrationRelation.AHEAD:
        if policy.allow_fast_forward:
            return IntegrationStrategy.FAST_FORWARD
        return (
            IntegrationStrategy.MERGE
            if merge_allowed
            else IntegrationStrategy.COMPLETE_IN_PLACE
        )
    if relation is IntegrationRelation.UNRELATED:
        return (
            IntegrationStrategy.MERGE
            if merge_allowed
            else IntegrationStrategy.COMPLETE_IN_PLACE
        )
    if policy.allow_rebase and _rebase_is_safe(shape):
        return IntegrationStrategy.REBASE
    return (
        IntegrationStrategy.MERGE
        if merge_allowed
        else IntegrationStrategy.COMPLETE_IN_PLACE
    )


@pytest.mark.parametrize("relation", _RELATIONS)
@pytest.mark.parametrize("shape", tuple(_SHAPES.values()), ids=tuple(_SHAPES))
@pytest.mark.parametrize("policy", tuple(_POLICIES.values()), ids=tuple(_POLICIES))
def test_strategy_choice_is_deterministic_across_relation_shape_and_policy(
    relation: IntegrationRelation,
    shape: IntegrationWorkShape,
    policy: IntegrationPolicy,
) -> None:
    observed = ObservedIntegration(relation=relation, work_shape=shape)

    decision = choose_integration_strategy(observed, policy)

    assert decision.strategy is _expected_strategy(relation, shape, policy)
    assert decision.reason.strip()
    assert decision.alternatives == tuple(
        strategy
        for strategy in _expected_candidates(relation)
        if strategy is not decision.strategy
    )


def test_already_shared_history_is_never_rewritten() -> None:
    observed = ObservedIntegration(
        relation=IntegrationRelation.DIVERGED,
        work_shape=IntegrationWorkShape(
            commit_count=2,
            history_already_published=True,
        ),
    )
    policy = IntegrationPolicy(
        allow_merge=False,
        allow_direct_integration=False,
        preserve_shared_history=False,
    )

    decision = choose_integration_strategy(observed, policy)

    assert decision.strategy is IntegrationStrategy.COMPLETE_IN_PLACE
    assert "never rewritten" in decision.reason.lower()


@pytest.mark.parametrize("relation", _RELATIONS)
def test_terminal_fallback_is_reachable_when_policy_disables_cheaper_strategies(
    relation: IntegrationRelation,
) -> None:
    observed = ObservedIntegration(
        relation=relation,
        work_shape=_SHAPES["many_linear_commits"],
    )

    decision = choose_integration_strategy(observed, _POLICIES["terminal_only"])

    assert decision.strategy is IntegrationStrategy.COMPLETE_IN_PLACE
    assert "completion in place" in decision.reason.lower()
    assert decision.reason.strip()


def test_transition_table_never_repeats_a_strategy_or_restarts_partial_work() -> None:
    assert STRATEGY_TRANSITIONS

    for transition in STRATEGY_TRANSITIONS:
        assert transition.next_strategy is not transition.current_strategy
        assert STRATEGY_ORDER.index(transition.next_strategy) > STRATEGY_ORDER.index(
            transition.current_strategy
        )
        assert transition.preserves_resolved_work
        assert not transition.restarts_from_zero
        assert transition.reason.strip()


def test_rebase_switch_to_merge_preserves_resolved_work() -> None:
    matching = [
        transition
        for transition in STRATEGY_TRANSITIONS
        if transition.current_strategy is IntegrationStrategy.REBASE
        and transition.guard is StrategyTransitionGuard.SHARED_HISTORY_DISCOVERED
    ]

    assert len(matching) == 1
    transition = matching[0]
    assert transition.next_strategy is IntegrationStrategy.MERGE
    assert transition.preserves_resolved_work
    assert not transition.restarts_from_zero


@pytest.mark.parametrize(
    "guard",
    (
        StrategyTransitionGuard.NEW_FEATURE_WORK,
        StrategyTransitionGuard.NEW_TARGET_WORK,
    ),
)
def test_new_work_transition_absorbs_work_without_discarding_progress(
    guard: StrategyTransitionGuard,
) -> None:
    matching = [
        transition
        for transition in STRATEGY_TRANSITIONS
        if transition.current_strategy is IntegrationStrategy.REBASE
        and transition.guard is guard
    ]

    assert len(matching) == 1
    transition = matching[0]
    assert transition.next_strategy is IntegrationStrategy.MERGE
    assert transition.preserves_resolved_work
    assert transition.absorbs_new_work
    assert not transition.restarts_from_zero


def test_completion_in_place_is_a_terminal_transition_state() -> None:
    assert not any(
        transition.current_strategy is IntegrationStrategy.COMPLETE_IN_PLACE
        for transition in STRATEGY_TRANSITIONS
    )
