
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommitReviewApproved:

    tree_sha: str
