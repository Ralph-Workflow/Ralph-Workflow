from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ralph.recovery.agent_failure_ledger import (
        AgentFailureLedger,
        AgentStatus,
        AttemptFact,
        AttemptOutcome,
        Cooldown,
        Eligible,
        Spent,
    )
    from ralph.recovery.agent_unavailability_tracker import (
        AgentUnavailabilityTracker,
        UnavailabilityEntry,
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
    "AgentFailureLedger",
    "AgentStatus",
    "AgentUnavailabilityTracker",
    "AttemptFact",
    "AttemptOutcome",
    "ClassifiedFailure",
    "ConnectivityMonitor",
    "ConnectivityState",
    "Cooldown",
    "CycleCap",
    "Eligible",
    "FailureCategory",
    "FailureClassifier",
    "FailureEvent",
    "FailureEventBus",
    "FalloverEvent",
    "ReasonBackoffPolicy",
    "RecoveryController",
    "Spent",
    "UnavailabilityEntry",
    "UnavailabilityReason",
    "compute_backoff_ms",
    "is_retryable_without_budget",
]

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "DEFAULT_UNAVAILABILITY_BACKOFF_POLICY": (
        "ralph.recovery.unavailability_reason",
        "DEFAULT_UNAVAILABILITY_BACKOFF_POLICY",
    ),
    "AgentFailureLedger": ("ralph.recovery.agent_failure_ledger", "AgentFailureLedger"),
    "AgentStatus": ("ralph.recovery.agent_failure_ledger", "AgentStatus"),
    "AgentUnavailabilityTracker": (
        "ralph.recovery.agent_unavailability_tracker",
        "AgentUnavailabilityTracker",
    ),
    "AttemptFact": ("ralph.recovery.agent_failure_ledger", "AttemptFact"),
    "AttemptOutcome": ("ralph.recovery.agent_failure_ledger", "AttemptOutcome"),
    "ClassifiedFailure": ("ralph.recovery.classifier", "ClassifiedFailure"),
    "ConnectivityMonitor": ("ralph.recovery.connectivity", "ConnectivityMonitor"),
    "ConnectivityState": ("ralph.recovery.connectivity", "ConnectivityState"),
    "Cooldown": ("ralph.recovery.agent_failure_ledger", "Cooldown"),
    "CycleCap": ("ralph.recovery.cycle_cap", "CycleCap"),
    "Eligible": ("ralph.recovery.agent_failure_ledger", "Eligible"),
    "FailureCategory": ("ralph.recovery.classifier", "FailureCategory"),
    "FailureClassifier": ("ralph.recovery.classifier", "FailureClassifier"),
    "FailureEvent": ("ralph.recovery.events", "FailureEvent"),
    "FailureEventBus": ("ralph.recovery.events", "FailureEventBus"),
    "FalloverEvent": ("ralph.recovery.events", "FalloverEvent"),
    "ReasonBackoffPolicy": ("ralph.recovery.unavailability_reason", "ReasonBackoffPolicy"),
    "RecoveryController": ("ralph.recovery.controller", "RecoveryController"),
    "Spent": ("ralph.recovery.agent_failure_ledger", "Spent"),
    "UnavailabilityEntry": (
        "ralph.recovery.agent_unavailability_tracker",
        "UnavailabilityEntry",
    ),
    "UnavailabilityReason": ("ralph.recovery.unavailability_reason", "UnavailabilityReason"),
    "compute_backoff_ms": ("ralph.recovery.controller", "compute_backoff_ms"),
    "is_retryable_without_budget": ("ralph.recovery.classifier", "is_retryable_without_budget"),
}


def __getattr__(name: str) -> object:
    try:
        module_name, attr_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg) from exc

    module = import_module(module_name)
    namespace = cast("dict[str, object]", module.__dict__)
    value = namespace[attr_name]
    cast("dict[str, object]", globals())[name] = value
    return value
