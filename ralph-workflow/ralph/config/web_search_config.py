"""Web search configuration models for `mcp.toml`."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.config._web_search_backend_spec import WebSearchBackendSpec
from ralph.pydantic_compat import RalphBaseModel


class WebSearchConfig(RalphBaseModel):
    """Top-level `web_search` config in `mcp.toml`."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    backend: str = "ddgs"
    fallback: list[str] = Field(default_factory=list)
    backends: dict[str, WebSearchBackendSpec] = Field(default_factory=dict)


__all__ = ["WebSearchConfig"]
