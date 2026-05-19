"""Web visit and media configuration models for `mcp.toml`."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel


class WebVisitConfig(RalphBaseModel):
    """Top-level `web_visit` config in `mcp.toml`."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    timeout_ms: int = Field(default=15000, gt=0)
    max_bytes: int = Field(default=2_097_152, gt=0)
    user_agent: str = "RalphWorkflow/1.0 (+https://ralph-workflow.dev)"
    allow_private_networks: bool = False
    extract_links: bool = False


__all__ = ["WebVisitConfig"]
