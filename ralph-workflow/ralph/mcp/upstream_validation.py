"""Upstream MCP validation - compatibility wrappers over the sub-package."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from ralph.mcp.protocol.startup import preflight_http_mcp_server_tools
from ralph.mcp.upstream import validation as _impl
from ralph.mcp.upstream.client import make_upstream_client
from ralph.mcp.upstream.validation import (
    UpstreamServerReport,
    UpstreamValidationError,
    UpstreamValidationReport,
    strict_mode_from_env,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from datetime import timedelta

    from ralph.mcp.upstream.config import UpstreamMcpServer
    from ralph.mcp.upstream.validation import HttpPreflightFn


class _ValidationModule(Protocol):
    make_upstream_client: Callable[[UpstreamMcpServer], object]

    def validate_upstream_mcp_servers(
        self,
        servers: Iterable[UpstreamMcpServer],
        *,
        timeout: timedelta | None = None,
        strict: bool | None = None,
        preflight_http: HttpPreflightFn = preflight_http_mcp_server_tools,
        list_stdio_tools: Callable[[UpstreamMcpServer, timedelta], list[str]] | None = None,
    ) -> UpstreamValidationReport: ...


_VALIDATION_IMPL = cast("_ValidationModule", _impl)


def validate_upstream_mcp_servers(
    servers: Iterable[UpstreamMcpServer],
    *,
    timeout: timedelta | None = None,
    strict: bool | None = None,
    preflight_http: HttpPreflightFn = preflight_http_mcp_server_tools,
    list_stdio_tools: Callable[[UpstreamMcpServer, timedelta], list[str]] | None = None,
) -> UpstreamValidationReport:
    _VALIDATION_IMPL.make_upstream_client = make_upstream_client
    return _VALIDATION_IMPL.validate_upstream_mcp_servers(
        servers,
        timeout=timeout,
        strict=strict,
        preflight_http=preflight_http,
        list_stdio_tools=list_stdio_tools,
    )


__all__ = [
    "UpstreamServerReport",
    "UpstreamValidationError",
    "UpstreamValidationReport",
    "make_upstream_client",
    "strict_mode_from_env",
    "validate_upstream_mcp_servers",
]
