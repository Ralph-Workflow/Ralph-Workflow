from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from ralph.mcp.artifacts.plan._step_target import StepTarget
from ralph.pydantic_compat import RalphBaseModel


class PlanStep(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int = Field(..., ge=1)
    title: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)
    step_type: Literal["file_change", "action", "research", "verify"] = "file_change"
    priority: Literal["critical", "high", "medium", "low"] | None = None
    targets: list[StepTarget] = Field(default_factory=list)
    location: str | None = None
    rationale: str | None = None
    depends_on: list[int] = Field(default_factory=list)
