"""Pydantic models for `mcp.toml`."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel

from .mcp_server_spec import McpServerSpec
from .web_search_config import WebSearchBackendSpec, WebSearchConfig
from .web_service_configs import MediaConfig, WebVisitConfig


class _FrozenMcpModel(RalphBaseModel):
    """Private base for frozen MCP config models.

    Owns ``model_config = ConfigDict(frozen=True)`` once so descendants do not
    repeat it. Pydantic v2 inherits ``model_config`` when descendants do not
    declare one of their own.
    """

    model_config = ConfigDict(frozen=True)


class McpConfig(_FrozenMcpModel):
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
