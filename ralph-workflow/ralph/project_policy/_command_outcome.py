"""One recorded gate run, ingested from a ``RALPH-EVIDENCE:`` line.

Single-class module per the repository's class-per-module policy.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    """One recorded gate run, ingested from a ``RALPH-EVIDENCE:`` line."""

    command: str
    exit_code: int
    duration_seconds: float
    policy_content_hash: str


__all__ = ["CommandOutcome"]
