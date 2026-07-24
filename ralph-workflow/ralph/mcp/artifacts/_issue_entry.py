"""_IssueEntry — validated issue entry for the issues artifact."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel


class _IssueEntry(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1)
    severity: str = Field(..., min_length=1)
    summary: str = Field(..., min_length=1)


__all__ = ["_IssueEntry"]
