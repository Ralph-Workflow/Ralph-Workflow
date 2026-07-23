"""The hardcoded policy pipeline: a frozen two-phase graph with an analysis loop.

This module is the policy pipeline's single source of truth for ROUTING. It is
pure: no I/O, no agents, no workspace. :mod:`ralph.project_policy.pipeline_driver`
executes what this module decides.

The graph::

    policy_remediation  --on_success-->  policy_remediation_analysis
                                                 |
                                       completed | -> done       (reset_loop)
                                 request_changes | -> remediation (loop += 1)
                                          failed | -> remediation (loop += 1)

Two rules bound it, and both are load-bearing:

**The deterministic validator is a hard gate.** ``done`` requires BOTH an empty
finding list from ``validators.validate_readiness`` AND a ``completed`` decision
from the analysis agent. While findings remain, the analysis phase is never
consulted at all — reviewing the QUALITY of a policy file that is still
structurally invalid is wasted work, and an analysis agent must never be in a
position where its ``completed`` could launder a failing validator.

**The analysis budget never blocks the run.** When the loop budget is spent, the
driver runs one final remediation and then walks FORWARD to the terminal phase
rather than failing -- the same exhausted-analysis bypass that
:func:`ralph.pipeline.handoffs.resolve_exhausted_analysis_bypass` applies to the
in-graph analysis phases. Project-policy readiness is best-effort; it is NOT a
precondition for doing development work. A run whose policy could not be made
ready proceeds to planning anyway, loudly, with its findings printed.

With the default cap of 3 the full loop is ``R A R A R A R``: four remediation
invocations, three analysis invocations, and the last remediation is the final
one before the run proceeds.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from ralph.project_policy.pipeline_phase import PolicyPhaseDef
from ralph.project_policy.pipeline_route import PolicyRoute

if TYPE_CHECKING:
    from collections.abc import Mapping

#: The remediation phase: writes and fixes the canonical policy files.
PHASE_REMEDIATION: Final[str] = "policy_remediation"

#: The analysis phase: reviews what remediation wrote and routes back or forward.
#:
#: Its ``analysis`` drain class grants NO workspace-write MCP tool -- no write, no
#: edit, no delete. It is not, however, hermetically read-only: the class does
#: grant ``process.exec_bounded`` so the agent can PROBE the gates the policy
#: declares, and a shell command can redirect. The guarantee that a review cannot
#: launder its own approval therefore rests on the deterministic re-validation in
#: :func:`ralph.project_policy.pipeline_driver._finish`, NOT on the tool surface.
PHASE_ANALYSIS: Final[str] = "policy_remediation_analysis"

#: The terminal phase. Reaching it always means "stop the policy pipeline and let
#: the run continue" -- never "abort the run".
TERMINAL_DONE: Final[str] = "done"

#: Analysis loop budget. Three review rounds is enough to converge a policy file
#: or to prove it will not converge; a fourth is a display flood, not a strategy.
DEFAULT_ANALYSIS_CAP: Final[int] = 3

#: ``R A R A R A R`` has one more remediation invocation than analysis rounds.
DEFAULT_MAX_REMEDIATION_ATTEMPTS: Final[int] = DEFAULT_ANALYSIS_CAP + 1

#: Decision the analysis agent returns to accept the policy as it stands.
DECISION_COMPLETED: Final[str] = "completed"

#: Decision the analysis agent returns to send concrete findings back to
#: remediation. The driver treats an unusable/absent/unparseable decision as
#: :data:`DECISION_FAILED`, never as :data:`DECISION_COMPLETED` (fail closed).
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

#: The phase graph, read-only. Exposed for tests and for the driver.
POLICY_PIPELINE: Final[Mapping[str, PolicyPhaseDef]] = MappingProxyType(
    _POLICY_PIPELINE
)


def phase_definition(phase: str) -> PolicyPhaseDef:
    """Return the definition of ``phase``.

    Raises:
        KeyError: When ``phase`` is not a phase of the policy pipeline. A typo
            in a phase name is a programming error, not a routing outcome, so it
            fails loudly rather than defaulting to some phase.
    """
    return _POLICY_PIPELINE[phase]


def resolve_decision(decision: str) -> PolicyRoute:
    """Map an analysis decision string onto its route.

    An unrecognized decision routes back to remediation exactly as ``failed``
    does. The analysis agent is a model, and a model can emit anything; the one
    outcome it must never be able to reach by accident or by fabrication is
    ``done``. Fail closed.
    """
    decisions = _POLICY_PIPELINE[PHASE_ANALYSIS].decisions
    return decisions.get(decision, decisions[DECISION_FAILED])


def analysis_budget_spent(iteration: int, cap: int) -> bool:
    """True when the analysis loop budget is exhausted.

    At that point the driver applies the exhausted-analysis bypass: it walks
    forward to :data:`TERMINAL_DONE` instead of entering the analysis phase
    again. Mirrors ``AnalysisLoopCounter.should_skip_reentry``
    (:mod:`ralph.pipeline.progress`).
    """
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
