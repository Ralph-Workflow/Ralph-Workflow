"""PhaseDecisionRoute Pydantic model."""

from __future__ import annotations

from pydantic import Field

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel


class PhaseDecisionRoute(_FrozenPolicyModel):
    """Route produced by an analysis decision."""

    target: str = Field(...)
    reset_loop: bool = False
    increments_counter: str | None = Field(
        default=None,
        description=(
            "Budget counter key to increment when this decision route is taken. "
            "None means no budget counter is incremented."
        ),
    )
