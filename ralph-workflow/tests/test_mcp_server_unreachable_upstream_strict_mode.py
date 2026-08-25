"""``RALPH_MCP_STRICT`` decides whether one unreachable upstream kills the server.

The env var is documented as the switch to warn-and-skip, but the standalone
server ignored it and always hard-failed. One unreachable custom MCP server
therefore aborted the whole subprocess before it could bind its port -- taking
every BUILT-IN Ralph tool down with it -- and the parent, which only sees a
refused connection, could not say why.

Strict remains the default (a server the operator configured and Ralph cannot
reach is still a loud failure). The escape hatch now actually works.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.config.mcp_models import McpConfig, McpServerSpec
from ralph.mcp.server import runtime as server_runtime
from ralph.mcp.tools.names import custom_proxy_tool_name
from ralph.mcp.upstream.config import (
    UPSTREAM_MCP_CONFIG_ENV,
    UPSTREAM_MCP_TOOL_CATALOG_ENV,
    UpstreamMcpServer,
)
from ralph.mcp.upstream.models import UpstreamCallError, UpstreamTool
from ralph.mcp.upstream.validation import UpstreamValidationError
from tests._support.typed_accessors import must_dict_list, must_mapping, must_str

if TYPE_CHECKING:
    from pathlib import Path

_SERVER_NAME = "docs-mcp"


@pytest.fixture(autouse=True)
def _isolate_from_upstream_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(UPSTREAM_MCP_CONFIG_ENV, raising=False)
    monkeypatch.delenv(UPSTREAM_MCP_TOOL_CATALOG_ENV, raising=False)


class _UnreachableClient:
    """An upstream client whose server never answers -- a stalled or absent binary."""

    def __init__(self, server: UpstreamMcpServer) -> None:
        self._server = server

    def list_tools(self) -> list[UpstreamTool]:
        raise UpstreamCallError(f"upstream server '{self._server.name}' timed out")

    def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        raise UpstreamCallError(f"upstream server '{self._server.name}' timed out")


def _install_unreachable_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    upstream = UpstreamMcpServer(name=_SERVER_NAME, transport="stdio", command=_SERVER_NAME)
    monkeypatch.setattr(
        server_runtime, "load_runtime_upstream_servers", lambda cfg: (upstream,)
    )
    monkeypatch.setattr("ralph.mcp.upstream.registry.make_upstream_client", _UnreachableClient)


def _build(tmp_path: Path) -> server_runtime.FallbackStandaloneServer:
    config = McpConfig(
        mcp_servers={
            _SERVER_NAME: McpServerSpec(
                name=_SERVER_NAME, transport="stdio", command=_SERVER_NAME
            )
        }
    )
    return server_runtime.build_standalone_http_server(
        tmp_path, extras=server_runtime.McpServerExtras(mcp_config=config)
    )


def _advertised_tool_names(server: server_runtime.FallbackStandaloneServer) -> set[str]:
    response, _state = server._mcp_server._handle_tools_list(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id=1, params={})
    )
    assert response is not None
    result = must_mapping(response.result)
    return {must_str(tool["name"]) for tool in must_dict_list(result["tools"])}


def test_soft_mode_serves_built_in_tools_without_the_unreachable_upstream(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RALPH_MCP_STRICT", "0")
    _install_unreachable_upstream(monkeypatch)

    tool_names = _advertised_tool_names(_build(tmp_path))

    assert tool_names
    proxy_prefix = custom_proxy_tool_name(_SERVER_NAME, "")
    assert not any(name.startswith(proxy_prefix) for name in tool_names)


def test_strict_mode_remains_the_default_and_names_the_unreachable_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("RALPH_MCP_STRICT", raising=False)
    _install_unreachable_upstream(monkeypatch)

    with pytest.raises(UpstreamValidationError, match=_SERVER_NAME):
        _build(tmp_path)
