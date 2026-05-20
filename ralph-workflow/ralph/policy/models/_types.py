"""Shared types and constants for policy models."""

from __future__ import annotations

from typing import Final, Literal

DrainName = str

PhaseRole = Literal[
    "execution",
    "analysis",
    "review",
    "commit",
    "verification",
    "terminal",
    "fanout_join",
    "commit_cleanup",
]

ROLE_REVIEW: Final[Literal["review"]] = "review"
