"""PostCommitRouteWhen Pydantic model."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel


class PostCommitRouteWhen(_FrozenPolicyModel):
    """Condition selector for post-commit budget-guarded routing."""

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
