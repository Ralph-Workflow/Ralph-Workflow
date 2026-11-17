from __future__ import annotations

from typing import Literal

from pydantic import Field

from ralph.pydantic_compat import RalphBaseModel


class PostCommitRouteWhen(RalphBaseModel):
    phase: str = Field(
        ...,
        description="Commit phase that this route applies to",
    )
    budget_state: Literal["remaining", "exhausted", "no_review"] = Field(
        ...,
        description=(
            "Budget state label: 'remaining' (budget>0), 'exhausted' (dev done, review remains), "
            "'no_review' (all budgets exhausted, no review to run)"
        ),
    )
    cycle_outcome: Literal["completed", "failed"] | None = Field(
        default=None,
        description="Optional cycle outcome required for this route.",
    )
