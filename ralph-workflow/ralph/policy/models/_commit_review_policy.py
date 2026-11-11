
from __future__ import annotations

from pydantic import Field

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel


class CommitReviewPolicy(_FrozenPolicyModel):

    max_deleted_files: int = Field(
        default=50,
        ge=0,
        description="Maximum allowed deleted files before mass-deletion hard tripwire triggers.",
    )
    max_deleted_fraction: float = Field(
        default=0.25,
        ge=0.0,
        le=1.0,
        description="Maximum allowed fraction of deleted files (0.0 to 1.0) before mass-deletion hard tripwire triggers.",
    )
    binary_size_limit: int = Field(
        default=1_000_000,
        gt=0,
        description="Maximum allowed binary file size in bytes before oversized-binary tripwire triggers.",
    )
    proposal_retention: int = Field(
        default=10,
        ge=1,
        description="Maximum number of recent proposal refs to retain before pruning.",
    )
    max_rejections: int = Field(
        default=2,
        ge=1,
        description="Maximum consecutive commit review rejections before failing the run.",
    )
