"""PhaseCommitPolicy Pydantic model."""

from __future__ import annotations

from pydantic import Field

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel


class PhaseCommitPolicy(_FrozenPolicyModel):
    """Commit semantics for commit-role phases."""

    requires_artifact: bool = True
    skipped_advances_progress: bool = True
    increments_counter: str | None = Field(
        default=None,
        description=(
            "Budget counter key (declared in pipeline.budget_counters) to bump on "
            "non-skipped commit. None means no counter is incremented."
        ),
    )
    loop_resets: list[str] = Field(default_factory=list)
