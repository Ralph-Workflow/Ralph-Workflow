"""GitLogParams dataclass for the git log MCP tool."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GitLogParams:
    """Parsed parameters for the git log tool."""

    count: int
