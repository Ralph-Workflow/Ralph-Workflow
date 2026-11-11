
from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from ralph.project_policy.pipeline_phase import PolicyPhaseDef
from ralph.project_policy.pipeline_route import PolicyRoute

if TYPE_CHECKING:
    from collections.abc import Mapping

PHASE_REMEDIATION: Final[str] = "policy_remediation"

PHASE_ANALYSIS: Final[str] = "policy_remediation_analysis"

TERMINAL_DONE: Final[str] = "done"

DEFAULT_ANALYSIS_CAP: Final[int] = 3

DEFAULT_MAX_REMEDIATION_ATTEMPTS: Final[int] = DEFAULT_ANALYSIS_CAP + 1

DECISION_COMPLETED: Final[str] = "completed"

DECISION_REQUEST_CHANGES: Final[str] = "request_changes"
DECISION_FAILED: Final[str] = "failed"

_POLICY_PIPELINE: Final[dict[str, PolicyPhaseDef]] = {
    PHASE_REMEDIATION: PolicyPhaseDef(
        drain=PHASE_REMEDIATION,
        role="execution",
        on_success=PHASE_ANALYSIS,
    ),
    PHASE_ANALYSIS: PolicyPhaseDef(
        drain=PHASE_ANALYSIS,
        role="analysis",
        decisions={
            DECISION_COMPLETED: PolicyRoute(target=TERMINAL_DONE, reset_loop=True),
            DECISION_REQUEST_CHANGES: PolicyRoute(target=PHASE_REMEDIATION),
            DECISION_FAILED: PolicyRoute(target=PHASE_REMEDIATION),
        },
    ),
}

POLICY_PIPELINE: Final[Mapping[str, PolicyPhaseDef]] = MappingProxyType(_POLICY_PIPELINE)


def phase_definition(phase: str) -> PolicyPhaseDef:
    return _POLICY_PIPELINE[phase]


def resolve_decision(decision: str) -> PolicyRoute:
    decisions = _POLICY_PIPELINE[PHASE_ANALYSIS].decisions
    return decisions.get(decision, decisions[DECISION_FAILED])


def analysis_budget_spent(iteration: int, cap: int) -> bool:
    return iteration >= cap


__all__ = [
    "DECISION_COMPLETED",
    "DECISION_FAILED",
    "DECISION_REQUEST_CHANGES",
    "DEFAULT_ANALYSIS_CAP",
    "DEFAULT_MAX_REMEDIATION_ATTEMPTS",
    "PHASE_ANALYSIS",
    "PHASE_REMEDIATION",
    "POLICY_PIPELINE",
    "TERMINAL_DONE",
    "analysis_budget_spent",
    "phase_definition",
    "resolve_decision",
]
