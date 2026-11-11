
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommitEffect:

    message_file: str
    expected_tree_sha: str | None = None
    require_approved_tree: bool = False
