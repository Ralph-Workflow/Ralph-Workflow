"""MCP server configuration model for `mcp.toml`."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.pydantic_compat import RalphBaseModel


class McpServerSpec(RalphBaseModel):
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


__all__ = ["McpServerSpec"]
