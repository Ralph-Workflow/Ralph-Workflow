"""CommitReviewPolicy Pydantic model."""

from __future__ import annotations

from pydantic import Field

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel


class CommitReviewPolicy(_FrozenPolicyModel):
    """Configuration for deterministic commit review tripwires and proposal retention.

    Attributes:
        max_deleted_files: Maximum allowed number of deleted files before
            triggering a mass-deletion hard tripwire. Defaults to 50.
        max_deleted_fraction: Maximum allowed ratio of deleted files to total
            tracked files (0.0 to 1.0) before triggering a mass-deletion hard
            tripwire. Defaults to 0.25 (25%).
        binary_size_limit: Maximum allowed size in bytes for binary files before
            triggering an oversized binary tripwire. Defaults to 1,000,000 bytes (1 MB).
        proposal_retention: Maximum number of recent proposal refs to retain
            before pruning. Defaults to 10.
        max_rejections: Maximum consecutive commit review rejections before
            failing the run. Defaults to 2.
    """

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
