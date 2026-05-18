from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel


class VerificationStep(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str = Field(..., min_length=1)
    expected_outcome: str = Field(..., min_length=1)
