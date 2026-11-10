"""Commit review gate failed event."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommitReviewGateFailed:
    """Commit review gate failed due to an invalid verdict, mismatch, or citation issue."""

    detail: str
