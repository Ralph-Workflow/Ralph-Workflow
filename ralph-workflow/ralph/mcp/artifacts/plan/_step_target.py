from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel


class StepTarget(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1)
    action: Literal["create", "modify", "delete", "read", "reference"]
