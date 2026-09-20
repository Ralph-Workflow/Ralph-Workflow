"""Structured commit-message detail limits."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommitMessageBudget:
    """Machine-readable limit for evidence-grounded commit body items."""

    tier: str
    max_body_points: int

    def __str__(self) -> str:
        descriptions = {
            "small": "one focused body point",
            "medium": "two or three focused body points",
            "large": "cover each material area, risks, and verification",
        }
        return f"{self.tier}: {descriptions[self.tier]}"
