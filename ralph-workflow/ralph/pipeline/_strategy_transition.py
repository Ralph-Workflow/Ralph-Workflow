from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline._integration_strategy import IntegrationStrategy
    from ralph.pipeline._strategy_transition_guard import StrategyTransitionGuard

__all__ = ["StrategyTransition"]


@dataclass(frozen=True, slots=True)
class StrategyTransition:
    current_strategy: IntegrationStrategy
    guard: StrategyTransitionGuard
    next_strategy: IntegrationStrategy
    preserves_resolved_work: bool
    restarts_from_zero: bool
    absorbs_new_work: bool
    reason: str
