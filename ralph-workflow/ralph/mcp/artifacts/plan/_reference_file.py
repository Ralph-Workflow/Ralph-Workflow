from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel


class ReferenceFile(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1, max_length=1000)
    purpose: str = Field(..., min_length=1, max_length=2000)
