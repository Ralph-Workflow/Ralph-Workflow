from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.mcp.artifacts.plan._scope_category import ScopeCategory
from ralph.pydantic_compat import RalphBaseModel


class ScopeItem(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1, description="Required scope item text (non-empty).")
    count: str | None = Field(
        default=None, description="Optional count or size hint (e.g. '3 files')."
    )
    category: ScopeCategory | None = Field(
        default=None,
        description="Optional ScopeCategory enum; see ScopeCategory literal.",
    )
