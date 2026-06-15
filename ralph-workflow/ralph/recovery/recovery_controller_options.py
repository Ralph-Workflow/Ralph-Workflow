"""Options dataclass for RecoveryController construction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:

    from ralph.agents.clock import Clock
    from ralph.policy.models import PolicyBundle
    from ralph.recovery.agent_budget_registry import AgentBudgetRegistry
    from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
    from ralph.recovery.events import FailureEventBus
    from ralph.recovery.failure_classifier import FailureClassifier
    from ralph.recovery.unavailability_reason import ReasonBackoffPolicy, UnavailabilityReason


@dataclass(frozen=True)
class RecoveryControllerOptions:
    """Options for constructing a RecoveryController."""

    cycle_cap: int = 200
    classifier: FailureClassifier | None = None
    event_bus: FailureEventBus | None = None
    budget_registry: AgentBudgetRegistry | None = None
    policy_bundle: PolicyBundle | None = None
    backoff_attempts: dict[str, int] | None = None
    technical_retry_cap: int = 10
    # Initial unavailable timeout state, keyed by "phase:agent" with values as
    # monotonic timestamps in milliseconds. Used to inject test state; in
    # production this starts empty and is populated by the controller.
    unavailable_timeouts: dict[str, int] | None = None
    # Per-reason backoff policy mapping. Defaults to
    # DEFAULT_UNAVAILABILITY_BACKOFF_POLICY when None.
    unavailability_backoff_policy: dict[UnavailabilityReason, ReasonBackoffPolicy] | None = None
    # Pre-seeded UnavailabilityEntry dict for testing the tracker directly.
    unavailability_entries: dict[str, UnavailabilityEntry] | None = None
    # Clock for time-dependent recovery decisions. Defaults to system clock in
    # production; inject FakeClock for deterministic tests.
    clock: Clock | None = None
