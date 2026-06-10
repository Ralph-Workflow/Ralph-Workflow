from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel


class VerificationStep(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str = Field(..., min_length=1)
    expected_outcome: str = Field(..., min_length=1)
    timeout_seconds: int | None = Field(default=None, gt=0, le=3600)
    cwd: str | None = Field(default=None, max_length=200)


__all__ = ["VerificationStep"]
