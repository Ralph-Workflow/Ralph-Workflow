"""Typed receipt for commit work that remains after publication."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CommitResidualEvent:
    """Identify committed and remaining paths after a commit."""

    committed_paths: tuple[str, ...]
    remaining_paths: tuple[str, ...]
    sha: str | None = None
