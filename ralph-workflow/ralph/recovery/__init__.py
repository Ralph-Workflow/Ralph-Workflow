from __future__ import annotations

from collections.abc import Callable

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


def _load_DEFAULT_UNAVAILABILITY_BACKOFF_POLICY() -> object:
    from ralph.recovery.unavailability_reason import DEFAULT_UNAVAILABILITY_BACKOFF_POLICY
    return DEFAULT_UNAVAILABILITY_BACKOFF_POLICY

def _load_AgentFailureLedger() -> object:
    from ralph.recovery.agent_failure_ledger import AgentFailureLedger
    return AgentFailureLedger

def _load_AgentStatus() -> object:
    from ralph.recovery.agent_failure_ledger import AgentStatus
    return AgentStatus

def _load_AgentUnavailabilityTracker() -> object:
    from ralph.recovery.agent_unavailability_tracker import AgentUnavailabilityTracker
    return AgentUnavailabilityTracker

def _load_AttemptFact() -> object:
    from ralph.recovery.agent_failure_ledger import AttemptFact
    return AttemptFact

def _load_AttemptOutcome() -> object:
    from ralph.recovery.agent_failure_ledger import AttemptOutcome
    return AttemptOutcome

def _load_ClassifiedFailure() -> object:
    from ralph.recovery.classifier import ClassifiedFailure
    return ClassifiedFailure

def _load_ConnectivityMonitor() -> object:
    from ralph.recovery.connectivity import ConnectivityMonitor
    return ConnectivityMonitor

def _load_ConnectivityState() -> object:
    from ralph.recovery.connectivity import ConnectivityState
    return ConnectivityState

def _load_Cooldown() -> object:
    from ralph.recovery.agent_failure_ledger import Cooldown
    return Cooldown

def _load_CycleCap() -> object:
    from ralph.recovery.cycle_cap import CycleCap
    return CycleCap

def _load_Eligible() -> object:
    from ralph.recovery.agent_failure_ledger import Eligible
    return Eligible

def _load_FailureCategory() -> object:
    from ralph.recovery.classifier import FailureCategory
    return FailureCategory

def _load_FailureClassifier() -> object:
    from ralph.recovery.classifier import FailureClassifier
    return FailureClassifier

def _load_FailureEvent() -> object:
    from ralph.recovery.events import FailureEvent
    return FailureEvent

def _load_FailureEventBus() -> object:
    from ralph.recovery.events import FailureEventBus
    return FailureEventBus

def _load_FalloverEvent() -> object:
    from ralph.recovery.events import FalloverEvent
    return FalloverEvent

def _load_ReasonBackoffPolicy() -> object:
    from ralph.recovery.unavailability_reason import ReasonBackoffPolicy
    return ReasonBackoffPolicy

def _load_RecoveryController() -> object:
    from ralph.recovery.controller import RecoveryController
    return RecoveryController

def _load_Spent() -> object:
    from ralph.recovery.agent_failure_ledger import Spent
    return Spent

def _load_UnavailabilityEntry() -> object:
    from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
    return UnavailabilityEntry

def _load_UnavailabilityReason() -> object:
    from ralph.recovery.unavailability_reason import UnavailabilityReason
    return UnavailabilityReason

def _load_compute_backoff_ms() -> object:
    from ralph.recovery.controller import compute_backoff_ms
    return compute_backoff_ms

def _load_is_retryable_without_budget() -> object:
    from ralph.recovery.classifier import is_retryable_without_budget
    return is_retryable_without_budget

_LOADERS: dict[str, Callable[[], object]] = {
    "DEFAULT_UNAVAILABILITY_BACKOFF_POLICY": _load_DEFAULT_UNAVAILABILITY_BACKOFF_POLICY,
    "AgentFailureLedger": _load_AgentFailureLedger,
    "AgentStatus": _load_AgentStatus,
    "AgentUnavailabilityTracker": _load_AgentUnavailabilityTracker,
    "AttemptFact": _load_AttemptFact,
    "AttemptOutcome": _load_AttemptOutcome,
    "ClassifiedFailure": _load_ClassifiedFailure,
    "ConnectivityMonitor": _load_ConnectivityMonitor,
    "ConnectivityState": _load_ConnectivityState,
    "Cooldown": _load_Cooldown,
    "CycleCap": _load_CycleCap,
    "Eligible": _load_Eligible,
    "FailureCategory": _load_FailureCategory,
    "FailureClassifier": _load_FailureClassifier,
    "FailureEvent": _load_FailureEvent,
    "FailureEventBus": _load_FailureEventBus,
    "FalloverEvent": _load_FalloverEvent,
    "ReasonBackoffPolicy": _load_ReasonBackoffPolicy,
    "RecoveryController": _load_RecoveryController,
    "Spent": _load_Spent,
    "UnavailabilityEntry": _load_UnavailabilityEntry,
    "UnavailabilityReason": _load_UnavailabilityReason,
    "compute_backoff_ms": _load_compute_backoff_ms,
    "is_retryable_without_budget": _load_is_retryable_without_budget,
}


def __getattr__(name: str) -> object:
    loader = _LOADERS.get(name)
    if loader is None:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    return loader()
