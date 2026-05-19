"""Explanation of a phase's commit policy."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CommitPolicyExplanation:
    """Explanation of a phase's commit policy."""

    increments_counter: str | None
    loop_resets: list[str]
    requires_artifact: bool
