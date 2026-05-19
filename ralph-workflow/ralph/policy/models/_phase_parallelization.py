"""PhaseParallelization Pydantic model."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel


class PhaseParallelization(_FrozenPolicyModel):
    """Transition-scoped parallelization policy for a pipeline phase."""

    mode: Literal["same_workspace"] = Field(
        default="same_workspace",
        description="Parallelization mode; only 'same_workspace' is supported in v1",
    )
    max_parallel_workers: int = Field(
        default=8,
        ge=1,
        description="Maximum allowed concurrent work units",
    )
    max_work_units: int = Field(
        default=50,
        ge=1,
        description="Maximum allowed total work units from planning artifact",
    )
    require_allowed_directories: bool = Field(
        default=True,
        description="Require each work unit to declare allowed_directories",
    )
    post_fanout_verification: bool = Field(
        default=False,
        description=(
            "When True, run a serialized workspace-wide verification step after all "
            "parallel workers complete. Defaults to False so unit tests never invoke "
            "make verify."
        ),
    )
