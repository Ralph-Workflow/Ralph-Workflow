"""Explanation of a single post-commit route entry."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PostCommitRouteExplanation:
    """Explanation of a single post-commit route entry."""

    phase: str
    budget_state: str
    target: str
