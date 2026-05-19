"""Evidence that a prior analysis item was addressed."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel


class AnalysisItemProof(RalphBaseModel):
    """Evidence that a prior analysis item was addressed."""

    model_config = ConfigDict(extra="forbid")

    how_to_fix_item: str = Field(..., min_length=1)
    proof: str = Field(..., min_length=1)
