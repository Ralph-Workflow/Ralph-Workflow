"""PhaseLoopPolicy Pydantic model."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel


class PhaseLoopPolicy(_FrozenPolicyModel):
    """Loop linkage for analysis phases."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    iteration_state_field: str = Field(...)
    loopback_review_outcome: str | None = None
