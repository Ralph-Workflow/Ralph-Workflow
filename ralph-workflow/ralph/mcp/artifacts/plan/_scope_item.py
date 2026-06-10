from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.mcp.artifacts.plan._scope_category import ScopeCategory
from ralph.pydantic_compat import RalphBaseModel


class ScopeItem(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1)
    count: str | None = None
    category: ScopeCategory | None = None
