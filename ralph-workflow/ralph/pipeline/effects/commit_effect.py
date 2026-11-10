"""Commit pipeline effect."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommitEffect:
    """Effect to create a git commit.

    Attributes:
        message_file: Path to the commit message file.
        expected_tree_sha: Optional approved git tree SHA for SHA-bound staging.
    """

    message_file: str
    expected_tree_sha: str | None = None
