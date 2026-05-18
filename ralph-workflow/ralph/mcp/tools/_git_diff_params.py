"""GitDiffParams dataclass for the git diff MCP tool."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GitDiffParams:
    """Parsed parameters for the git diff tool."""

    args: list[str]
