"""GitLogParams dataclass for the git log MCP tool."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Phase 4: format is closed to ``raw`` (default; preserved legacy output)
# and ``summary`` (compact JSON envelope). Unknown values raise
# ``InvalidParamsError`` in ``parse_git_log_params`` so a malformed
# value never reaches the git subprocess.
GitLogFormat = Literal["raw", "summary"]


@dataclass(frozen=True)
class GitLogParams:
    """Parsed parameters for the git log tool."""

    count: int
    format: GitLogFormat = "raw"
