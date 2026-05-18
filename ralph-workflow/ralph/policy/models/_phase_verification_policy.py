"""PhaseVerificationPolicy Pydantic model."""

from __future__ import annotations

from typing import Literal

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel


class PhaseVerificationPolicy(_FrozenPolicyModel):
    """Verification gating semantics for a phase."""

    kind: Literal["artifact", "none"]
    gate_for: Literal["advancement", "completion", "release"]
    on_failure_route: str | None = None
