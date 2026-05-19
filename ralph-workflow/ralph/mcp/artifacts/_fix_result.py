"""FixResult — validated schema for a fix_result artifact payload."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel


class FixResult(RalphBaseModel):
    """Validated schema for a fix_result artifact payload."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(..., min_length=1)
    files_changed: str = Field(..., min_length=1)
    next_steps: str | None = None


__all__ = ["FixResult"]
