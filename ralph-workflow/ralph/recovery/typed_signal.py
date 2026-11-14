from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer
from ralph.recovery.attribution import liveness_observed

__all__ = ["TypedSignal", "runtime_issued_termination_is_not_agent_fault"]

_RUNTIME_ISSUERS: Final[frozenset[TerminationIssuer]] = frozenset(
    {
        TerminationIssuer.QUOTA_DETECTOR,
        TerminationIssuer.SUPERVISOR_IDLE,
        TerminationIssuer.RECOVERY_TIMEOUT,
        TerminationIssuer.POST_EXIT_WATCHDOG,
        TerminationIssuer.OPERATOR,
    }
)


@dataclass(frozen=True)
class TypedSignal:
    name: str
    provider_originated: bool = False
    agent_initiated: bool = False


def runtime_issued_termination_is_not_agent_fault(facts: AttemptFacts) -> bool:
    termination = facts.termination
    if termination is None:
        return False
    if termination.issuer is TerminationIssuer.QUOTA_DETECTOR and not liveness_observed(facts):
        return False
    return termination.issuer in _RUNTIME_ISSUERS
