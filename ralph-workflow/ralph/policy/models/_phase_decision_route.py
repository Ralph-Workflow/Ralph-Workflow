"""PhaseDecisionRoute Pydantic model."""

from __future__ import annotations

from pydantic import Field

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel


class PhaseDecisionRoute(_FrozenPolicyModel):
    """Route produced by an analysis decision."""

    target: str = Field(...)
    reset_loop: bool = False
