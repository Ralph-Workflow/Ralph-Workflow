
from __future__ import annotations

from pydantic import Field

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel

EXECUTION_RESULT_STATUSES: tuple[str, ...] = ("completed", "partial", "failed")


class PhaseInvocationGate(_FrozenPolicyModel):

    upstream_execution_phase: str
    minimum_elapsed_seconds: float
    always_invoke_statuses: list[str] = Field(
        default_factory=list,
        description=(
            "Execution result statuses that bypass the minimum-elapsed gate "
            "and always enter analysis (e.g. ['partial', 'failed']). "
            "Valid values are the closed vocabulary declared by the upstream "
            "execution artifact: " + ", ".join(EXECUTION_RESULT_STATUSES) + "."
        ),
    )
