"""Commit review loopback event."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommitReviewLoopback:
    """Commit review requested an agent loopback with a hint."""

    hint: str
