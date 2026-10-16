"""R2: build_standalone_http_server raises McpServerError before any registry work
when RALPH_MCP_RESPAWN=1 and UPSTREAM_MCP_TOOL_CATALOG is absent.

The contract:
  - is_respawn AND no tool_catalog → McpServerError with last_stage="respawn_precheck"
    BEFORE any UpstreamRegistry.build() (zero list_tools calls to upstreams).
  - is_respawn AND tool_catalog present → UpstreamRegistry.build_from_tool_catalog path
    (zero list_tools calls, no McpServerError).

These are unit tests that mock all network-facing components. They verify the
correct error type, message, and last_stage is raised at the right point in
the call sequence — without assuming implementation internals beyond the
public build_standalone_http_server API.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server.runtime import McpServerExtras
from ralph.mcp.upstream.client import HttpUpstreamClient, JsonRpcCaller
from ralph.mcp.upstream.config import UpstreamMcpServer
from ralph.mcp.upstream.models import UpstreamTool

if TYPE_CHECKING:
    from ralph.config.mcp_models import McpConfig
    from ralph.mcp.protocol.session import AgentSession
    from ralph.mcp.upstream.registry import UpstreamClientFactory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(
    session_id: str = "test-session",
    run_id: str = "test-run",
    capabilities: set[str] | None = None,
) -> AgentSession:
    """Build a minimal real AgentSession with the attributes runtime reads."""
    from ralph.mcp.protocol.session import AgentSession

    return AgentSession(
        session_id=session_id,
        run_id=run_id,
        drain="standalone",
        capabilities=capabilities or {"mcp"},
    )


def _make_mcp_config() -> McpConfig:
    """Build a minimal real McpConfig with all-default fields."""
    from ralph.config.mcp_models import McpConfig

    return McpConfig()


def _make_upstream_server(name: str = "remote") -> UpstreamMcpServer:
    """Build a minimal real upstream server definition."""
    return UpstreamMcpServer(name=name, transport="http", url="http://unused.example/mcp")


def _counting_caller(counter: list[int]) -> JsonRpcCaller:
    """JSON-RPC caller that records every tools/list probe in ``counter``."""

    def caller(method: str, _payload: dict[str, object]) -> dict[str, object]:
        if method == "tools/list":
            counter.append(1)
            return {
                "tools": [
                    {
                        "name": "fake-tool",
                        "description": "fake",
                        "inputSchema": {"type": "object"},
                    }
                ]
            }
        return {}

    return caller


def _make_client_factory(counter: list[int]) -> UpstreamClientFactory:
    """Client factory producing real HttpUpstreamClients with a counting caller."""

    def factory(server: UpstreamMcpServer) -> HttpUpstreamClient:
        return HttpUpstreamClient(server, caller=_counting_caller(counter))

    return factory


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_respawn_without_catalog_raises_before_discovery() -> None:
    """RALPH_MCP_RESPAWN=1 with no UPSTREAM_MCP_TOOL_CATALOG must raise
    McpServerError with last_stage='respawn_precheck' BEFORE any list_tools
    call is made on any upstream client."""
    counter: list[int] = []
    servers = [_make_upstream_server("remote")]

    with patch(
        "ralph.mcp.server.runtime.load_upstream_mcp_servers",
        return_value=servers,
    ), patch(
        "ralph.mcp.upstream.registry.make_upstream_client",
        _make_client_factory(counter),
    ):
        from ralph.mcp.server.runtime import build_standalone_http_server

        env: dict[str, str] = {"RALPH_MCP_RESPAWN": "1"}
        with pytest.raises(McpServerError) as exc_info:
            build_standalone_http_server(
                workspace_root=Path("/tmp"),
                extras=McpServerExtras(
                    session=_make_session(),
                    upstream_registry=None,
                    mcp_config=_make_mcp_config(),
                ),
                env=env,
            )

    assert exc_info.value.last_stage == "respawn_precheck"
    assert counter == [], (
        "list_tools must not have been called: the respawn precheck must fire "
        "before UpstreamRegistry.build() runs any network I/O"
    )


def test_respawn_without_catalog_message_mentions_respawn() -> None:
    """The error message must be informative for an operator to understand the contract violation."""
    servers = [_make_upstream_server()]
    with patch(
        "ralph.mcp.server.runtime.load_upstream_mcp_servers",
        return_value=servers,
    ):
        from ralph.mcp.server.runtime import build_standalone_http_server

        env: dict[str, str] = {"RALPH_MCP_RESPAWN": "1"}
        with pytest.raises(McpServerError) as exc_info:
            build_standalone_http_server(
                workspace_root=Path("/tmp"),
                extras=McpServerExtras(
                    session=_make_session(),
                    upstream_registry=None,
                    mcp_config=_make_mcp_config(),
                ),
                env=env,
            )

    msg = str(exc_info.value)
    assert "respawn" in msg.lower() or "RALPH_MCP_RESPAWN" in msg


def test_respawn_without_catalog_last_stage_is_respawn_precheck() -> None:
    """last_stage must be 'respawn_precheck' so the supervisor can record the precise stage."""
    servers = [_make_upstream_server()]
    with patch(
        "ralph.mcp.server.runtime.load_upstream_mcp_servers",
        return_value=servers,
    ):
        from ralph.mcp.server.runtime import build_standalone_http_server

        env: dict[str, str] = {"RALPH_MCP_RESPAWN": "1"}
        with pytest.raises(McpServerError) as exc_info:
            build_standalone_http_server(
                workspace_root=Path("/tmp"),
                extras=McpServerExtras(
                    session=_make_session(),
                    upstream_registry=None,
                    mcp_config=_make_mcp_config(),
                ),
                env=env,
            )

    assert exc_info.value.last_stage == "respawn_precheck"


def test_respawn_with_catalog_uses_build_from_tool_catalog() -> None:
    """When RESPAWN=1 AND UPSTREAM_MCP_TOOL_CATALOG is present,
    build_from_tool_catalog is called (zero list_tools calls)."""
    counter: list[int] = []
    servers = [_make_upstream_server("remote")]
    catalog_tool = UpstreamTool(name="alpha", description="d")
    catalog: dict[str, list[UpstreamTool]] = {"remote": [catalog_tool]}
    with patch(
        "ralph.mcp.server.runtime.load_upstream_mcp_servers",
        return_value=servers,
    ), patch(
        "ralph.mcp.server.runtime.load_upstream_tool_catalog",
        return_value=catalog,
    ), patch(
        "ralph.mcp.upstream.registry.make_upstream_client",
        _make_client_factory(counter),
    ):
        from ralph.mcp.server.runtime import build_standalone_http_server

        env: dict[str, str] = {"RALPH_MCP_RESPAWN": "1"}
        build_standalone_http_server(
            workspace_root=Path("/tmp"),
            extras=McpServerExtras(
                session=_make_session(),
                upstream_registry=None,
                mcp_config=_make_mcp_config(),
            ),
            env=env,
        )

    assert counter == [], (
        "build_from_tool_catalog must not call list_tools; "
        "a respawn child must do zero network I/O before serving"
    )


def test_respawn_with_empty_upstream_servers_no_error() -> None:
    """When RESPAWN=1 but no upstreams are configured (empty list),
    no error should be raised because there is nothing to probe."""
    no_servers: list[UpstreamMcpServer] = []
    with patch(
        "ralph.mcp.server.runtime.load_upstream_mcp_servers",
        return_value=no_servers,
    ):
        from ralph.mcp.server.runtime import build_standalone_http_server

        env: dict[str, str] = {"RALPH_MCP_RESPAWN": "1"}
        build_standalone_http_server(
            workspace_root=Path("/tmp"),
            extras=McpServerExtras(
                session=_make_session(),
                upstream_registry=None,
                mcp_config=_make_mcp_config(),
            ),
            env=env,
        )


def test_non_respawn_first_start_does_not_trigger_respawn_check() -> None:
    """A first-start (RESPAWN not set) must not trigger the respawn precheck."""
    no_servers: list[UpstreamMcpServer] = []
    with patch(
        "ralph.mcp.server.runtime.load_upstream_mcp_servers",
        return_value=no_servers,
    ):
        from ralph.mcp.server.runtime import build_standalone_http_server

        env: dict[str, str] = {}  # No RESPAWN — first start path
        build_standalone_http_server(
            workspace_root=Path("/tmp"),
            extras=McpServerExtras(
                session=_make_session(),
                upstream_registry=None,
                mcp_config=_make_mcp_config(),
            ),
            env=env,
        )
