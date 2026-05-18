"""Pydantic models for `mcp.toml`."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel

from .mcp_server_spec import McpServerSpec
from .web_search_config import WebSearchBackendSpec, WebSearchConfig
from .web_service_configs import MediaConfig, WebVisitConfig


class McpConfig(RalphBaseModel):
    model_config = ConfigDict(frozen=True)
    """Top-level `mcp.toml` document."""

    mcp_servers: dict[str, McpServerSpec] = Field(default_factory=dict)
    web_search: WebSearchConfig = Field(default_factory=WebSearchConfig)
    web_visit: WebVisitConfig = Field(default_factory=WebVisitConfig)
    media: MediaConfig = Field(default_factory=MediaConfig)


__all__ = [
    "McpConfig",
    "McpServerSpec",
    "MediaConfig",
    "WebSearchBackendSpec",
    "WebSearchConfig",
    "WebVisitConfig",
]
