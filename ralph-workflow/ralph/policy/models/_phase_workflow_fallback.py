
from __future__ import annotations

from pydantic import Field

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel


class PhaseWorkflowFallback(_FrozenPolicyModel):

    target: str = Field(..., description="Phase to route to when the agent chain is exhausted")
    note: str | None = Field(
        default=None,
        description="Optional rationale for this fallback, shown by --explain-policy",
    )
