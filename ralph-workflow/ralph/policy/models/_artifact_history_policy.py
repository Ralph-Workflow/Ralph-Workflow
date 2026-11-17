from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel


class ArtifactHistoryPolicy(_FrozenPolicyModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Whether to archive prior artifact versions before overwrite",
    )
    clear_on_fresh_entry: bool = Field(
        default=True,
        description="Whether a fresh phase entry clears old history (not a loopback)",
    )
