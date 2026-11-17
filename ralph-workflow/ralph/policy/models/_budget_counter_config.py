from __future__ import annotations

from pydantic import Field

from ralph.pydantic_compat import RalphBaseModel


class BudgetCounterConfig(RalphBaseModel):
    description: str = Field(default="", description="Human-readable description")
    tracks_budget: bool = Field(
        default=True,
        description="Whether remaining budget is tracked for post-commit routing",
    )
    default_max: int = Field(
        ...,
        ge=0,
        description="Default maximum budget — required so the runtime never invents a hidden cap",
    )
