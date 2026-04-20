"""Agent transport probe - compatibility wrappers over the sub-package."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, cast

from ralph.agents.transport_emit import _build_opencode_provider_config, _claude_mcp_config
from ralph.mcp.upstream import agent_probe as _impl
from ralph.mcp.upstream.agent_probe import (
    AgentProbeReport,
    AgentTransportProbeError,
    _augment_codex_config_with_server,
    _http_handshake,
    _server_handshake,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import timedelta
    from pathlib import Path

    from ralph.config.enums import AgentTransport
    from ralph.mcp.upstream.config import UpstreamMcpServer


class _ProbeModule(Protocol):
    _augment_codex_config_with_server: object
    _build_opencode_provider_config: object
    _claude_mcp_config: object
    _http_handshake: object
    _server_handshake: object
    _DEFAULT_TRANSPORTS: tuple[AgentTransport, ...]

    def probe_agent_transports(
        self,
        servers: Iterable[UpstreamMcpServer],
        *,
        transports: Iterable[AgentTransport] = ...,
        workspace_path: Path | None = None,
        timeout: timedelta | None = None,
    ) -> tuple[AgentProbeReport, ...]: ...


_PROBE_IMPL = cast("_ProbeModule", _impl)


def probe_agent_transports(
    servers: Iterable[UpstreamMcpServer],
    *,
    transports: Iterable[AgentTransport] = _impl._DEFAULT_TRANSPORTS,
    workspace_path: Path | None = None,
    timeout: timedelta | None = None,
) -> tuple[AgentProbeReport, ...]:
    _PROBE_IMPL._augment_codex_config_with_server = _augment_codex_config_with_server
    _PROBE_IMPL._build_opencode_provider_config = _build_opencode_provider_config
    _PROBE_IMPL._claude_mcp_config = _claude_mcp_config
    _PROBE_IMPL._http_handshake = _http_handshake
    _PROBE_IMPL._server_handshake = _server_handshake
    return _PROBE_IMPL.probe_agent_transports(
        servers,
        transports=transports,
        workspace_path=workspace_path,
        timeout=timeout,
    )


__all__ = [
    "AgentProbeReport",
    "AgentTransportProbeError",
    "_augment_codex_config_with_server",
    "_build_opencode_provider_config",
    "_claude_mcp_config",
    "_http_handshake",
    "_server_handshake",
    "probe_agent_transports",
]
