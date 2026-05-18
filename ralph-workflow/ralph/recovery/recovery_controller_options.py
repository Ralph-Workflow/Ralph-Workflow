"""Options dataclass for RecoveryController construction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle
    from ralph.recovery.agent_budget_registry import AgentBudgetRegistry
    from ralph.recovery.events import FailureEventBus
    from ralph.recovery.failure_classifier import FailureClassifier


@dataclass(frozen=True)
class RecoveryControllerOptions:
    """Options for constructing a RecoveryController."""

    cycle_cap: int = 200
    classifier: FailureClassifier | None = None
    event_bus: FailureEventBus | None = None
    budget_registry: AgentBudgetRegistry | None = None
    policy_bundle: PolicyBundle | None = None
    backoff_attempts: dict[str, int] | None = None
