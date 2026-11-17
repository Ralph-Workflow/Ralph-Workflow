from __future__ import annotations

from pydantic import Field

from ralph.pydantic_compat import RalphBaseModel


class LoopCounterConfig(RalphBaseModel):
    default_max: int = Field(default=3, ge=0, description="Default maximum iterations")
    description: str = Field(default="", description="Human-readable description")
