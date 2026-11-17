from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
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


def _load_default_unavailability_backoff_policy() -> object:
    from ralph.recovery.unavailability_reason import DEFAULT_UNAVAILABILITY_BACKOFF_POLICY
    return DEFAULT_UNAVAILABILITY_BACKOFF_POLICY

def _load_agentfailureledger() -> object:
    from ralph.recovery.agent_failure_ledger import AgentFailureLedger
    return AgentFailureLedger

def _load_agentstatus() -> object:
    from ralph.recovery.agent_failure_ledger import AgentStatus
    return AgentStatus

def _load_agentunavailabilitytracker() -> object:
    from ralph.recovery.agent_unavailability_tracker import AgentUnavailabilityTracker
    return AgentUnavailabilityTracker

def _load_attemptfact() -> object:
    from ralph.recovery.agent_failure_ledger import AttemptFact
    return AttemptFact

def _load_attemptoutcome() -> object:
    from ralph.recovery.agent_failure_ledger import AttemptOutcome
    return AttemptOutcome

def _load_classifiedfailure() -> object:
    from ralph.recovery.classifier import ClassifiedFailure
    return ClassifiedFailure

def _load_connectivitymonitor() -> object:
    from ralph.recovery.connectivity import ConnectivityMonitor
    return ConnectivityMonitor

def _load_connectivitystate() -> object:
    from ralph.recovery.connectivity import ConnectivityState
    return ConnectivityState

def _load_cooldown() -> object:
    from ralph.recovery.agent_failure_ledger import Cooldown
    return Cooldown

def _load_cyclecap() -> object:
    from ralph.recovery.cycle_cap import CycleCap
    return CycleCap

def _load_eligible() -> object:
    from ralph.recovery.agent_failure_ledger import Eligible
    return Eligible

def _load_failurecategory() -> object:
    from ralph.recovery.classifier import FailureCategory
    return FailureCategory

def _load_failureclassifier() -> object:
    from ralph.recovery.classifier import FailureClassifier
    return FailureClassifier

def _load_failureevent() -> object:
    from ralph.recovery.events import FailureEvent
    return FailureEvent

def _load_failureeventbus() -> object:
    from ralph.recovery.events import FailureEventBus
    return FailureEventBus

def _load_falloverevent() -> object:
    from ralph.recovery.events import FalloverEvent
    return FalloverEvent

def _load_reasonbackoffpolicy() -> object:
    from ralph.recovery.unavailability_reason import ReasonBackoffPolicy
    return ReasonBackoffPolicy

def _load_recoverycontroller() -> object:
    from ralph.recovery.controller import RecoveryController
    return RecoveryController

def _load_spent() -> object:
    from ralph.recovery.agent_failure_ledger import Spent
    return Spent

def _load_unavailabilityentry() -> object:
    from ralph.recovery.agent_unavailability_tracker import UnavailabilityEntry
    return UnavailabilityEntry

def _load_unavailabilityreason() -> object:
    from ralph.recovery.unavailability_reason import UnavailabilityReason
    return UnavailabilityReason

def _load_compute_backoff_ms() -> object:
    from ralph.recovery.controller import compute_backoff_ms
    return compute_backoff_ms

def _load_is_retryable_without_budget() -> object:
    from ralph.recovery.classifier import is_retryable_without_budget
    return is_retryable_without_budget

_LOADERS: dict[str, Callable[[], object]] = {
    "DEFAULT_UNAVAILABILITY_BACKOFF_POLICY": _load_default_unavailability_backoff_policy,
    "AgentFailureLedger": _load_agentfailureledger,
    "AgentStatus": _load_agentstatus,
    "AgentUnavailabilityTracker": _load_agentunavailabilitytracker,
    "AttemptFact": _load_attemptfact,
    "AttemptOutcome": _load_attemptoutcome,
    "ClassifiedFailure": _load_classifiedfailure,
    "ConnectivityMonitor": _load_connectivitymonitor,
    "ConnectivityState": _load_connectivitystate,
    "Cooldown": _load_cooldown,
    "CycleCap": _load_cyclecap,
    "Eligible": _load_eligible,
    "FailureCategory": _load_failurecategory,
    "FailureClassifier": _load_failureclassifier,
    "FailureEvent": _load_failureevent,
    "FailureEventBus": _load_failureeventbus,
    "FalloverEvent": _load_falloverevent,
    "ReasonBackoffPolicy": _load_reasonbackoffpolicy,
    "RecoveryController": _load_recoverycontroller,
    "Spent": _load_spent,
    "UnavailabilityEntry": _load_unavailabilityentry,
    "UnavailabilityReason": _load_unavailabilityreason,
    "compute_backoff_ms": _load_compute_backoff_ms,
    "is_retryable_without_budget": _load_is_retryable_without_budget,
}


def __getattr__(name: str) -> object:
    loader = _LOADERS.get(name)
    if loader is None:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    return loader()
