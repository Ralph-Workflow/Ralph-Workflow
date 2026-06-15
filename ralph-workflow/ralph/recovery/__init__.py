"""Pipeline recovery: failure classification, budgets, connectivity, and retry control.

This package coordinates the recovery cycle that runs after a phase fails. It decides
whether to retry the phase, escalate, or abort based on failure classification and
remaining budget.

Main entry points:

- ``RecoveryController`` — top-level controller; evaluates a failure and returns a
  recovery action (retry, fallover, abort). Injected with a ``FailureClassifier`` and
  an ``AgentBudgetRegistry``.
- ``FailureClassifier``, ``ClassifiedFailure``, ``FailureCategory`` — classify a raw
  failure string into a category (agent_error, environment, connectivity, ambiguous, …).
  ``is_retryable_without_budget()`` identifies failures that bypass the budget counter.
- ``AgentBudgetRegistry``, ``FailureBudget``, ``BudgetState``, ``seed_budget_registry``
  — per-agent retry budgets; prevent infinite retry loops.
- ``ConnectivityMonitor``, ``ConnectivityState`` — background connectivity probe that
  signals the runner to pause when the host loses network access.
- ``CycleCap`` — enforces the pipeline-level ``cycle_cap`` limit from recovery policy.
- ``FailureEvent``, ``FailureEventBus``, ``FalloverEvent`` — event types emitted when
  the recovery controller fires; consumed by the display and logging subsystems.
- ``compute_backoff_ms`` — computes the exponential backoff delay for the next retry.
"""

from __future__ import annotations

from ralph.recovery.agent_unavailability_tracker import (
    AgentUnavailabilityTracker,
    UnavailabilityEntry,
)
from ralph.recovery.budget import (
    AgentBudgetRegistry,
    BudgetState,
    FailureBudget,
    seed_budget_registry,
)
from ralph.recovery.classifier import (
    ClassifiedFailure,
    FailureCategory,
    FailureClassifier,
    is_retryable_without_budget,
)
from ralph.recovery.connectivity import ConnectivityMonitor, ConnectivityState
from ralph.recovery.controller import RecoveryController, compute_backoff_ms
from ralph.recovery.cycle_cap import CycleCap
from ralph.recovery.events import FailureEvent, FailureEventBus, FalloverEvent
from ralph.recovery.unavailability_reason import (
    DEFAULT_UNAVAILABILITY_BACKOFF_POLICY,
    ReasonBackoffPolicy,
    UnavailabilityReason,
)

__all__ = [
    "DEFAULT_UNAVAILABILITY_BACKOFF_POLICY",
    "AgentBudgetRegistry",
    "AgentUnavailabilityTracker",
    "BudgetState",
    "ClassifiedFailure",
    "ConnectivityMonitor",
    "ConnectivityState",
    "CycleCap",
    "FailureBudget",
    "FailureCategory",
    "FailureClassifier",
    "FailureEvent",
    "FailureEventBus",
    "FalloverEvent",
    "ReasonBackoffPolicy",
    "RecoveryController",
    "UnavailabilityEntry",
    "UnavailabilityReason",
    "compute_backoff_ms",
    "is_retryable_without_budget",
    "seed_budget_registry",
]
