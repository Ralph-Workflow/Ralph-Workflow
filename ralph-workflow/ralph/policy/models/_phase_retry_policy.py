"""PhaseRetryPolicy Pydantic model."""

from __future__ import annotations

from pydantic import Field

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel


class PhaseRetryPolicy(_FrozenPolicyModel):
    """Per-phase retry policy overriding chain-level defaults."""

    max_retries: int = Field(default=3, ge=0)
    retry_delay_ms: int = Field(default=1000, ge=0)
    retry_in_session: bool = False
