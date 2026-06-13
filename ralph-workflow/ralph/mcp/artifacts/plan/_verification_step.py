from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel


class VerificationStep(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Verification method (non-empty, max 2000 chars; medium tier).",
    )
    expected_outcome: str = Field(
        ...,
        min_length=1,
        max_length=8000,
        description="Expected outcome string (non-empty, max 8000 chars; medium tier).",
    )
    timeout_seconds: int | None = Field(
        default=None,
        gt=0,
        le=3600,
        description="Optional timeout in seconds (0 < value <= 3600).",
    )
    cwd: str | None = Field(
        default=None,
        max_length=500,
        description="Optional working directory string (max 500 chars; short tier).",
    )


__all__ = ["VerificationStep"]
