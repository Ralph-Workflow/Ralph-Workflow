"""GitShowParams dataclass for the git show MCP tool."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Phase 4: format is closed to ``raw`` (default; preserved legacy output)
# and ``summary`` (compact header-only envelope). Unknown values raise
# ``InvalidParamsError`` in ``parse_git_show_params``.
GitShowFormat = Literal["raw", "summary"]


@dataclass(frozen=True)
class GitShowParams:
    """Parsed parameters for the git show tool."""

    git_ref: str
    format: GitShowFormat = "raw"
