"""_IssueEntry — validated issue entry for the issues artifact."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel


class _IssueEntry(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1)
    severity: Literal["high", "medium", "low"]
    summary: str = Field(..., min_length=1)


__all__ = ["_IssueEntry"]
