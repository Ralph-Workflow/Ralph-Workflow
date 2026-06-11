from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel


class VerificationStep(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str = Field(..., min_length=1, description="Verification method (non-empty).")
    expected_outcome: str = Field(
        ..., min_length=1, description="Expected outcome string (non-empty)."
    )
    timeout_seconds: int | None = Field(
        default=None,
        gt=0,
        le=3600,
        description="Optional timeout in seconds (0 < value <= 3600).",
    )
    cwd: str | None = Field(
        default=None,
        max_length=200,
        description="Optional working directory string (max 200 chars).",
    )


__all__ = ["VerificationStep"]
