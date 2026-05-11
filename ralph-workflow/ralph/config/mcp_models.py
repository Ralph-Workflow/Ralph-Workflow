"""Pydantic models for `mcp.toml`."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.pydantic_compat import RalphBaseModel

_DEFAULT_MAX_INLINE_BYTES = 5_242_880  # 5 MiB


class _FrozenMcpModel(RalphBaseModel):
    """Private base for frozen MCP config models.

    Owns ``model_config = ConfigDict(frozen=True)`` once so descendants do not
    repeat it. Pydantic v2 inherits ``model_config`` when descendants do not
    declare one of their own.
    """

    model_config = ConfigDict(frozen=True)


class McpServerSpec(_FrozenMcpModel):
    """Schema for a single MCP server entry in `mcp.toml`."""

    name: str = Field(..., pattern=r"^[a-z][a-z0-9_-]*$")
    transport: Literal["http", "stdio"]
    url: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    chains: list[str] | None = Field(
        default=None,
        description=(
            "Reserved for v2; MUST be None in v1 (schema-forward-compatible, runtime ignores)."
        ),
    )

    @model_validator(mode="after")
    def validate_name(self) -> Self:
        if self.name == RALPH_MCP_SERVER_NAME:
            raise ValueError(f"server name '{RALPH_MCP_SERVER_NAME}' is reserved")
        if "__" in self.name:
            raise ValueError("server name must not contain '__'")
        return self

    @model_validator(mode="after")
    def validate_transport_fields(self) -> Self:
        if self.transport == "http":
            if not self.url:
                raise ValueError("http transport requires url")
            if self.command:
                raise ValueError("http transport must not set command")
            if self.args:
                raise ValueError("http transport must not set args")
        elif self.transport == "stdio":
            if not self.command:
                raise ValueError("stdio transport requires command")
            if self.url:
                raise ValueError("stdio transport must not set url")
        return self


class WebSearchBackendSpec(_FrozenMcpModel):
    """Backend configuration for built-in web search providers."""

    backend: Literal["ddgs", "searxng", "tavily", "brave", "exa"]
    url: str | None = None
    api_key: str | None = None
    api_key_env: str | None = None

    @model_validator(mode="after")
    def validate_secret_shape(self) -> Self:
        if self.backend == "searxng" and not self.url:
            raise ValueError("searxng backend requires url")
        if self.backend in {"tavily", "brave", "exa"}:
            has_api_key = self.api_key is not None
            has_api_key_env = self.api_key_env is not None
            if has_api_key == has_api_key_env:
                raise ValueError("api_key xor api_key_env is required for keyed backends")
        return self


class WebSearchConfig(_FrozenMcpModel):
    """Top-level `web_search` config in `mcp.toml`."""

    enabled: bool = True
    backend: str = "ddgs"
    fallback: list[str] = Field(default_factory=list)
    backends: dict[str, WebSearchBackendSpec] = Field(default_factory=dict)


class WebVisitConfig(_FrozenMcpModel):
    """Top-level `web_visit` config in `mcp.toml`."""

    enabled: bool = True
    timeout_ms: int = Field(default=15000, gt=0)
    max_bytes: int = Field(default=2_097_152, gt=0)
    user_agent: str = "RalphWorkflow/1.0 (+https://ralph-workflow.dev)"
    allow_private_networks: bool = False
    extract_links: bool = False


class MediaConfig(_FrozenMcpModel):
    """Multimodal media support config in `mcp.toml`.

    Broad multimodal support (images, PDFs, audio, video, documents) is enabled
    by default. Disable with ``[media] enabled = false`` in ``mcp.toml``.
    """

    enabled: bool = True
    max_inline_bytes: int = Field(default=_DEFAULT_MAX_INLINE_BYTES, gt=0)


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
