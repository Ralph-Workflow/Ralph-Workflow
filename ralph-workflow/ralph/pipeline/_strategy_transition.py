from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.pipeline._integration_relation import IntegrationRelation
from ralph.pipeline._strategy_transition_guard import StrategyTransitionGuard

if TYPE_CHECKING:
    from ralph.pipeline._integration_strategy import IntegrationStrategy
    from ralph.pipeline._observed_integration import ObservedIntegration

__all__ = ["StrategyTransition", "apply_strategy_transition"]


@dataclass(frozen=True, slots=True)
class StrategyTransition:
    current_strategy: IntegrationStrategy
    guard: StrategyTransitionGuard
    next_strategy: IntegrationStrategy
    preserves_resolved_work: bool
    restarts_from_zero: bool
    absorbs_new_work: bool
    reason: str


def _fired_guards(
    observed: ObservedIntegration,
    attempts: int,
) -> tuple[StrategyTransitionGuard, ...]:
    shape = observed.work_shape
    fired: list[StrategyTransitionGuard] = []
    if shape.history_already_published:
        fired.append(StrategyTransitionGuard.SHARED_HISTORY_DISCOVERED)
    if shape.hot_conflict_repetition > 0:
        fired.append(StrategyTransitionGuard.REPEATED_HOT_CONFLICT)
    if attempts > 1:
        fired.append(StrategyTransitionGuard.REPLAY_RANGE_SHIFTED)
    if observed.relation is IntegrationRelation.DIVERGED:
        fired.append(StrategyTransitionGuard.NEW_FEATURE_WORK)
    if observed.relation in (IntegrationRelation.BEHIND, IntegrationRelation.DIVERGED):
        fired.append(StrategyTransitionGuard.NEW_TARGET_WORK)
    return tuple(fired)


def apply_strategy_transition(
    current_strategy: IntegrationStrategy,
    *,
    observed: ObservedIntegration,
    attempts: int,
) -> StrategyTransition | None:
    from ralph.pipeline.integration_strategy import STRATEGY_TRANSITIONS

    fired = _fired_guards(observed, attempts)
    if not fired:
        return None
    for transition in STRATEGY_TRANSITIONS:
        if transition.current_strategy is not current_strategy:
            continue
        if transition.guard in fired:
            return transition
    return None
