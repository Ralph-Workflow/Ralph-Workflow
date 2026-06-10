from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.mcp.artifacts.plan._scope_item import ScopeItem
from ralph.pydantic_compat import RalphBaseModel


class Summary(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    context: str = Field(default="", max_length=2000)
    scope_items: list[ScopeItem] = Field(..., min_length=3)
