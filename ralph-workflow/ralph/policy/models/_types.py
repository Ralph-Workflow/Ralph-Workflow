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
    "commit_review",
]

ROLE_REVIEW: Final[Literal["review"]] = "review"
