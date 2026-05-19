"""Evidence that a plan item was completed."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel


class PlanItemProof(RalphBaseModel):
    """Evidence that a plan item was completed."""

    model_config = ConfigDict(extra="forbid")

    plan_item: str = Field(..., min_length=1)
    proof: str = Field(..., min_length=1)
