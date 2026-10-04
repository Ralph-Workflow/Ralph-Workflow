from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel


class CriticalPrimaryFile(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1, max_length=1000)
    action: str = Field(..., min_length=1, max_length=200)
    estimated_changes: str | None = Field(default=None, max_length=500)
