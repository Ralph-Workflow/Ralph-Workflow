"""Pydantic models for `mcp.toml`."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME

_DEFAULT_MAX_INLINE_BYTES = 5_242_880  # 5 MiB


class McpServerSpec(BaseModel):  # type: ignore[explicit-any]
    """Schema for a single MCP server entry in `mcp.toml`."""

    model_config = ConfigDict(frozen=True)

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


class WebSearchBackendSpec(BaseModel):  # type: ignore[explicit-any]
    """Backend configuration for built-in web search providers."""

    model_config = ConfigDict(frozen=True)

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


class WebSearchConfig(BaseModel):  # type: ignore[explicit-any]
    """Top-level `web_search` config in `mcp.toml`."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    backend: str = "ddgs"
    fallback: list[str] = Field(default_factory=list)
    backends: dict[str, WebSearchBackendSpec] = Field(default_factory=dict)


class MediaConfig(BaseModel):  # type: ignore[explicit-any]
    """Opt-in multimodal media support config in `mcp.toml`.

    Multimodal support is disabled by default. Enable via [media] section:
        [media]
        enabled = true
        max_inline_bytes = 5242880
    """

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    max_inline_bytes: int = Field(default=_DEFAULT_MAX_INLINE_BYTES, gt=0)


class McpConfig(BaseModel):  # type: ignore[explicit-any]
    """Top-level `mcp.toml` document."""

    model_config = ConfigDict(frozen=True)

    mcp_servers: dict[str, McpServerSpec] = Field(default_factory=dict)
    web_search: WebSearchConfig = Field(default_factory=WebSearchConfig)
    media: MediaConfig = Field(default_factory=MediaConfig)


__all__ = [
    "McpConfig",
    "McpServerSpec",
    "MediaConfig",
    "WebSearchBackendSpec",
    "WebSearchConfig",
]
