"""Pydantic models for `mcp.toml`."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel

from ._media_config import MediaConfig
from ._web_search_backend_spec import WebSearchBackendSpec
from .mcp_server_spec import McpServerSpec
from .web_search_config import WebSearchConfig
from .web_service_configs import WebVisitConfig


class McpConfig(RalphBaseModel):
    """Top-level `mcp.toml` document."""

    model_config = ConfigDict(frozen=True)

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
