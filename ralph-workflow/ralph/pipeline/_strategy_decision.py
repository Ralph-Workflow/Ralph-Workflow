from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.integration_strategy import IntegrationStrategy

__all__ = ["StrategyDecision"]


@dataclass(frozen=True, slots=True)
class StrategyDecision:
    strategy: IntegrationStrategy
    reason: str
    alternatives: tuple[IntegrationStrategy, ...]
