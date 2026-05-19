"""PhaseTransition Pydantic model."""

from __future__ import annotations

from pydantic import Field

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel


class PhaseTransition(_FrozenPolicyModel):
    """Transition rules from a phase to other phases."""

    on_success: str = Field(..., description="Next phase on success")
    on_failure: str | None = Field(
        default=None, description="Next phase on failure (None = fail pipeline)"
    )
    on_loopback: str | None = Field(
        default=None,
        description="Next phase on loopback/continue signal (e.g., more iterations)",
    )
