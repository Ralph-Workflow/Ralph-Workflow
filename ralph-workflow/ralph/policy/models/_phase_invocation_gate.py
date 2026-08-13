"""PhaseInvocationGate model for duration-gated analysis invocation."""

from __future__ import annotations

from pydantic import Field

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel

# The closed status vocabulary declared by the upstream execution artifact
# (development_result).  Used by validators to reject unknown
# always-invoke statuses at policy-load time.
EXECUTION_RESULT_STATUSES: tuple[str, ...] = ("completed", "partial", "failed")


class PhaseInvocationGate(_FrozenPolicyModel):
    """Duration and status gate controlling whether an analysis phase is invoked.

    When declared on an analysis phase, the post-commit routing seam sums
    matching phase-timing records' unrounded ``elapsed.total_seconds()``
    for ``upstream_execution_phase`` from the current cycle's start index.
    Analysis is invoked only when the cumulative time meets or exceeds
    ``minimum_elapsed_seconds`` **or** the upstream execution result's
    status is listed in ``always_invoke_statuses``; otherwise the analysis
    phase is bypassed and routing continues to its policy-declared success
    route without consuming an analysis-loop cycle.
    """

    upstream_execution_phase: str
    minimum_elapsed_seconds: float
    always_invoke_statuses: list[str] = Field(
        default_factory=list,
        description=(
            "Execution result statuses that bypass the minimum-elapsed gate "
            "and always enter analysis (e.g. ['partial', 'failed']). "
            "Valid values are the closed vocabulary declared by the upstream "
            "execution artifact: "
            + ", ".join(EXECUTION_RESULT_STATUSES)
            + "."
        ),
    )
