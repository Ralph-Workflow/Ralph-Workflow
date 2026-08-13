"""PhaseInvocationGate model for duration-gated analysis invocation."""

from __future__ import annotations

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel


class PhaseInvocationGate(_FrozenPolicyModel):
    """Duration gate controlling whether an analysis phase is invoked.

    When declared on an analysis phase, the post-commit routing seam sums
    matching phase-timing records' unrounded ``elapsed.total_seconds()``
    for ``upstream_execution_phase`` from the current cycle's start index.
    Analysis is invoked only when the cumulative time meets or exceeds
    ``minimum_elapsed_seconds``; otherwise the analysis phase is bypassed
    and routing continues to its policy-declared success route without
    consuming an analysis-loop cycle.
    """

    upstream_execution_phase: str
    minimum_elapsed_seconds: float
