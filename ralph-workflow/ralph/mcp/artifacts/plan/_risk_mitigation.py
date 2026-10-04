"""Risk-mitigation sub-model for the plan artifact schema."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel


class RiskMitigation(RalphBaseModel):
    """A single identified risk and its mitigating action."""

    model_config = ConfigDict(extra="forbid")

    risk: str = Field(..., min_length=1, max_length=8000)
    mitigation: str = Field(default="", max_length=8000)
    severity: str | None = Field(default=None, min_length=1, max_length=200)
