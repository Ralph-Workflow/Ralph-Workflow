from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.pipeline._integration_strategy import IntegrationStrategy

if TYPE_CHECKING:
    from ralph.pipeline.auto_integrate_record import IntegrationRecord

__all__ = ["StrategyDecision", "record_strategy_decision"]


@dataclass(frozen=True, slots=True)
class StrategyDecision:
    strategy: IntegrationStrategy
    reason: str
    alternatives: tuple[IntegrationStrategy, ...]


def record_strategy_decision(record: IntegrationRecord) -> StrategyDecision:
    recorded = record.integrated_strategy
    if recorded is None:
        raise ValueError("the durable integration record carries no recorded strategy")
    reason = record.integrated_strategy_reason
    return StrategyDecision(
        strategy=IntegrationStrategy(recorded),
        reason=reason if reason is not None else "the durable record carries no recorded reason",
        alternatives=(),
    )
