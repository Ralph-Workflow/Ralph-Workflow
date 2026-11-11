
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommitReviewRejected:

    reason: str
    ref: str
    hard: bool = False
