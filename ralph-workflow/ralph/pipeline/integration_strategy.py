from __future__ import annotations

from typing import Final

from ralph.pipeline._integration_policy import IntegrationPolicy
from ralph.pipeline._integration_relation import IntegrationRelation
from ralph.pipeline._integration_strategy import IntegrationStrategy
from ralph.pipeline._integration_work_shape import IntegrationWorkShape
from ralph.pipeline._observed_integration import ObservedIntegration
from ralph.pipeline._strategy_decision import StrategyDecision
from ralph.pipeline._strategy_transition import StrategyTransition
from ralph.pipeline._strategy_transition_guard import StrategyTransitionGuard

__all__ = [
    "STRATEGY_ORDER",
    "STRATEGY_TRANSITIONS",
    "IntegrationPolicy",
    "IntegrationRelation",
    "IntegrationStrategy",
    "IntegrationWorkShape",
    "ObservedIntegration",
    "StrategyDecision",
    "StrategyTransition",
    "StrategyTransitionGuard",
    "choose_integration_strategy",
]

STRATEGY_ORDER: Final[tuple[IntegrationStrategy, ...]] = (
    IntegrationStrategy.NOTHING_TO_DO,
    IntegrationStrategy.FAST_FORWARD,
    IntegrationStrategy.REBASE,
    IntegrationStrategy.MERGE,
    IntegrationStrategy.COMPLETE_IN_PLACE,
)

STRATEGY_TRANSITIONS: Final[tuple[StrategyTransition, ...]] = (
    StrategyTransition(
        current_strategy=IntegrationStrategy.REBASE,
        guard=StrategyTransitionGuard.SHARED_HISTORY_DISCOVERED,
        next_strategy=IntegrationStrategy.MERGE,
        preserves_resolved_work=True,
        restarts_from_zero=False,
        absorbs_new_work=False,
        reason="Shared history was discovered, so merge preserves it and every resolved hunk.",
    ),
    StrategyTransition(
        current_strategy=IntegrationStrategy.REBASE,
        guard=StrategyTransitionGuard.REPEATED_HOT_CONFLICT,
        next_strategy=IntegrationStrategy.MERGE,
        preserves_resolved_work=True,
        restarts_from_zero=False,
        absorbs_new_work=False,
        reason="Repeated conflict on the same content makes merge safer than another replay.",
    ),
    StrategyTransition(
        current_strategy=IntegrationStrategy.REBASE,
        guard=StrategyTransitionGuard.REPLAY_RANGE_SHIFTED,
        next_strategy=IntegrationStrategy.MERGE,
        preserves_resolved_work=True,
        restarts_from_zero=False,
        absorbs_new_work=False,
        reason="The replay range shifted, so merge continues from the resolved state without replaying it.",
    ),
    StrategyTransition(
        current_strategy=IntegrationStrategy.REBASE,
        guard=StrategyTransitionGuard.NEW_FEATURE_WORK,
        next_strategy=IntegrationStrategy.MERGE,
        preserves_resolved_work=True,
        restarts_from_zero=False,
        absorbs_new_work=True,
        reason="New feature work is absorbed by merge while all resolved work is preserved.",
    ),
    StrategyTransition(
        current_strategy=IntegrationStrategy.REBASE,
        guard=StrategyTransitionGuard.NEW_TARGET_WORK,
        next_strategy=IntegrationStrategy.MERGE,
        preserves_resolved_work=True,
        restarts_from_zero=False,
        absorbs_new_work=True,
        reason="New target work is absorbed by merge while all resolved work is preserved.",
    ),
    StrategyTransition(
        current_strategy=IntegrationStrategy.REBASE,
        guard=StrategyTransitionGuard.REBASE_DISALLOWED_BY_POLICY,
        next_strategy=IntegrationStrategy.MERGE,
        preserves_resolved_work=True,
        restarts_from_zero=False,
        absorbs_new_work=False,
        reason="Policy forbids rebase, so merge continues from the resolved state.",
    ),
    StrategyTransition(
        current_strategy=IntegrationStrategy.MERGE,
        guard=StrategyTransitionGuard.SHARED_HISTORY_DISCOVERED,
        next_strategy=IntegrationStrategy.COMPLETE_IN_PLACE,
        preserves_resolved_work=True,
        restarts_from_zero=False,
        absorbs_new_work=False,
        reason="Shared history remains protected and completion in place preserves the resolved state.",
    ),
    StrategyTransition(
        current_strategy=IntegrationStrategy.MERGE,
        guard=StrategyTransitionGuard.REPEATED_HOT_CONFLICT,
        next_strategy=IntegrationStrategy.COMPLETE_IN_PLACE,
        preserves_resolved_work=True,
        restarts_from_zero=False,
        absorbs_new_work=False,
        reason="Repeated conflicts make completion in place the terminal autonomous strategy.",
    ),
    StrategyTransition(
        current_strategy=IntegrationStrategy.MERGE,
        guard=StrategyTransitionGuard.REPLAY_RANGE_SHIFTED,
        next_strategy=IntegrationStrategy.COMPLETE_IN_PLACE,
        preserves_resolved_work=True,
        restarts_from_zero=False,
        absorbs_new_work=False,
        reason="A shifting replay range is completed in place from the preserved work.",
    ),
    StrategyTransition(
        current_strategy=IntegrationStrategy.MERGE,
        guard=StrategyTransitionGuard.NEW_FEATURE_WORK,
        next_strategy=IntegrationStrategy.COMPLETE_IN_PLACE,
        preserves_resolved_work=True,
        restarts_from_zero=False,
        absorbs_new_work=True,
        reason="Completion in place absorbs new feature work without discarding resolved work.",
    ),
    StrategyTransition(
        current_strategy=IntegrationStrategy.MERGE,
        guard=StrategyTransitionGuard.NEW_TARGET_WORK,
        next_strategy=IntegrationStrategy.COMPLETE_IN_PLACE,
        preserves_resolved_work=True,
        restarts_from_zero=False,
        absorbs_new_work=True,
        reason="Completion in place absorbs new target work without discarding resolved work.",
    ),
    StrategyTransition(
        current_strategy=IntegrationStrategy.MERGE,
        guard=StrategyTransitionGuard.MERGE_DISALLOWED_BY_POLICY,
        next_strategy=IntegrationStrategy.COMPLETE_IN_PLACE,
        preserves_resolved_work=True,
        restarts_from_zero=False,
        absorbs_new_work=False,
        reason="Policy forbids merge, so completion in place is the terminal autonomous strategy.",
    ),
    StrategyTransition(
        current_strategy=IntegrationStrategy.FAST_FORWARD,
        guard=StrategyTransitionGuard.NEW_FEATURE_WORK,
        next_strategy=IntegrationStrategy.MERGE,
        preserves_resolved_work=True,
        restarts_from_zero=False,
        absorbs_new_work=True,
        reason="New feature work is absorbed by merge rather than repeating the fast-forward strategy.",
    ),
    StrategyTransition(
        current_strategy=IntegrationStrategy.FAST_FORWARD,
        guard=StrategyTransitionGuard.NEW_TARGET_WORK,
        next_strategy=IntegrationStrategy.MERGE,
        preserves_resolved_work=True,
        restarts_from_zero=False,
        absorbs_new_work=True,
        reason="New target work is absorbed by merge rather than repeating the fast-forward strategy.",
    ),
    StrategyTransition(
        current_strategy=IntegrationStrategy.FAST_FORWARD,
        guard=StrategyTransitionGuard.FAST_FORWARD_DISALLOWED_BY_POLICY,
        next_strategy=IntegrationStrategy.MERGE,
        preserves_resolved_work=True,
        restarts_from_zero=False,
        absorbs_new_work=False,
        reason="Policy forbids fast-forward, so merge continues without discarding progress.",
    ),
    StrategyTransition(
        current_strategy=IntegrationStrategy.NOTHING_TO_DO,
        guard=StrategyTransitionGuard.NEW_FEATURE_WORK,
        next_strategy=IntegrationStrategy.REBASE,
        preserves_resolved_work=True,
        restarts_from_zero=False,
        absorbs_new_work=True,
        reason="New feature work makes rebase necessary while preserving all existing work.",
    ),
    StrategyTransition(
        current_strategy=IntegrationStrategy.NOTHING_TO_DO,
        guard=StrategyTransitionGuard.NEW_TARGET_WORK,
        next_strategy=IntegrationStrategy.REBASE,
        preserves_resolved_work=True,
        restarts_from_zero=False,
        absorbs_new_work=True,
        reason="New target work makes rebase necessary while preserving all existing work.",
    ),
)


def _merge_allowed(policy: IntegrationPolicy) -> bool:
    return policy.allow_merge or policy.allow_direct_integration


def _merge_mode(policy: IntegrationPolicy) -> str:
    if policy.allow_merge and policy.allow_direct_integration:
        return "merge or direct integration"
    if policy.allow_merge:
        return "merge"
    return "direct integration"


def _rebase_blockers(
    shape: IntegrationWorkShape,
    policy: IntegrationPolicy,
) -> tuple[str, ...]:
    blockers: list[str] = []
    if not policy.allow_rebase:
        blockers.append("policy forbids rebase")
    if shape.history_already_published:
        if policy.preserve_shared_history:
            blockers.append("policy preserves already-shared history, which is never rewritten")
        else:
            blockers.append("already-shared history is never rewritten")
    if shape.contains_merge_commits:
        blockers.append("the range contains merge commits that replay would rewrite")
    if shape.many_commits:
        blockers.append(
            f"many commits ({shape.commit_count}) make replay more expensive than direct integration"
        )
    if shape.conflicts_expected:
        blockers.append("conflicts are expected during replay")
    if shape.conflicts_seen:
        blockers.append("conflicts have already been seen during replay")
    if shape.hot_conflict_repetition > 0:
        blockers.append("the same conflict has repeated")
    return tuple(blockers)


def _decision(
    strategy: IntegrationStrategy,
    reason: str,
    candidates: tuple[IntegrationStrategy, ...],
) -> StrategyDecision:
    alternatives = tuple(candidate for candidate in candidates if candidate is not strategy)
    return StrategyDecision(strategy=strategy, reason=reason, alternatives=alternatives)


def _terminal_decision(
    detail: str,
    candidates: tuple[IntegrationStrategy, ...],
) -> StrategyDecision:
    return _decision(
        IntegrationStrategy.COMPLETE_IN_PLACE,
        f"Completion in place is selected because {detail}; it preserves current work and "
        "needs neither an agent nor a human.",
        candidates,
    )


def _equal_decision(policy: IntegrationPolicy) -> StrategyDecision:
    candidates = (
        IntegrationStrategy.NOTHING_TO_DO,
        IntegrationStrategy.COMPLETE_IN_PLACE,
    )
    if policy.allow_nothing_to_do:
        return _decision(
            IntegrationStrategy.NOTHING_TO_DO,
            "Feature and target are equal, so the integration goal is already satisfied.",
            candidates,
        )
    return _terminal_decision("policy disallows the nothing-to-do strategy", candidates)


def _ahead_decision(policy: IntegrationPolicy) -> StrategyDecision:
    candidates = (
        IntegrationStrategy.FAST_FORWARD,
        IntegrationStrategy.MERGE,
        IntegrationStrategy.COMPLETE_IN_PLACE,
    )
    if policy.allow_fast_forward:
        return _decision(
            IntegrationStrategy.FAST_FORWARD,
            "Feature is strictly ahead of target, so fast-forward is the cheapest safe integration.",
            candidates,
        )
    if _merge_allowed(policy):
        return _decision(
            IntegrationStrategy.MERGE,
            f"Policy forbids fast-forward, so {_merge_mode(policy)} carries all feature work forward.",
            candidates,
        )
    return _terminal_decision(
        "policy forbids every strategy cheaper than completion in place",
        candidates,
    )


def _rebased_relation_decision(
    relation: IntegrationRelation,
    shape: IntegrationWorkShape,
    policy: IntegrationPolicy,
) -> StrategyDecision:
    candidates = (
        IntegrationStrategy.REBASE,
        IntegrationStrategy.MERGE,
        IntegrationStrategy.COMPLETE_IN_PLACE,
    )
    blockers = _rebase_blockers(shape, policy)
    if not blockers:
        relation_reason = (
            "Feature is behind target"
            if relation is IntegrationRelation.BEHIND
            else "Feature and target have diverged"
        )
        return _decision(
            IntegrationStrategy.REBASE,
            f"{relation_reason}; rebase is allowed and the work shape is safe to replay, so "
            "rebase is the cheapest way to carry all feature work forward.",
            candidates,
        )
    if _merge_allowed(policy):
        blocker_reason = "; ".join(blockers)
        return _decision(
            IntegrationStrategy.MERGE,
            f"Rebase is not the cheapest safe strategy because {blocker_reason}; "
            f"{_merge_mode(policy)} preserves existing history and carries all work forward.",
            candidates,
        )
    blocker_reason = "; ".join(blockers)
    return _terminal_decision(
        f"policy and work shape rule out every cheaper strategy: {blocker_reason}",
        candidates,
    )


def _unrelated_decision(policy: IntegrationPolicy) -> StrategyDecision:
    candidates = (
        IntegrationStrategy.MERGE,
        IntegrationStrategy.COMPLETE_IN_PLACE,
    )
    if _merge_allowed(policy):
        return _decision(
            IntegrationStrategy.MERGE,
            f"Unrelated histories make replay less safe, so {_merge_mode(policy)} carries all "
            "feature work forward without rewriting it.",
            candidates,
        )
    return _terminal_decision(
        "policy forbids merge and direct integration",
        candidates,
    )


def choose_integration_strategy(
    observed: ObservedIntegration,
    policy: IntegrationPolicy,
) -> StrategyDecision:
    match observed.relation:
        case IntegrationRelation.EQUAL:
            return _equal_decision(policy)
        case IntegrationRelation.AHEAD:
            return _ahead_decision(policy)
        case IntegrationRelation.BEHIND | IntegrationRelation.DIVERGED:
            return _rebased_relation_decision(
                observed.relation,
                observed.work_shape,
                policy,
            )
        case IntegrationRelation.UNRELATED:
            return _unrelated_decision(policy)
