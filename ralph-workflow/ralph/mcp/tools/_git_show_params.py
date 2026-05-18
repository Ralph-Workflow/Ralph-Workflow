"""GitShowParams dataclass for the git show MCP tool."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GitShowParams:
    """Parsed parameters for the git show tool."""

    git_ref: str
