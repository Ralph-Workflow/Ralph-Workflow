from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel


class RiskMitigation(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    risk: str = Field(..., min_length=1, max_length=8000)
    mitigation: str = Field(..., min_length=1, max_length=8000)
    severity: Literal["low", "medium", "high", "critical"] | None = None
