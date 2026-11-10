"""Commit review rejected event."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommitReviewRejected:
    """Commit review rejected the pending proposal."""

    reason: str
    ref: str
    hard: bool = False
