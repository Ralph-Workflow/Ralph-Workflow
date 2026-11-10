"""Commit review approved event."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommitReviewApproved:
    """Commit review approved the pending proposal."""

    tree_sha: str
