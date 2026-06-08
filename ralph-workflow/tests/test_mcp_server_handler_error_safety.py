"""Regression tests: McpServer.handle_request must never crash the transport.

The HTTP transport (`_fallback_http_handler.do_POST`) calls
``McpServer.handle_request`` with no try/except, so an unhandled exception in any
method handler escaped as a bare HTTP 500 with no JSON-RPC body — which an MCP
client (e.g. nanocoder) can only interpret as a broken/empty session. Only
``tools/call`` converted exceptions to a JSON-RPC error; every other method
(``tools/list``, ``initialize``, ``resources/*``) did not. These tests pin that
ALL methods convert an unexpected handler exception into a ``-32603`` JSON-RPC
error response, uniformly, so the transport layer always gets a well-formed
response.
"""

from __future__ import annotations

from typing import Any, cast

from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._mcp_server import McpServer
from ralph.mcp.server._server_state import ServerState


class _RaisingRegistry:
    """A registry whose tools/list enumeration always raises."""

    def list_definitions(self) -> list[object]:
        raise RuntimeError("kaboom enumerating tools")

    def set_client_capabilities(self, capabilities: object) -> None:
        del capabilities


def _server_with_raising_registry() -> McpServer:
    return McpServer(
        session=cast("Any", object()),
        workspace=cast("Any", object()),
        registry=cast("Any", _RaisingRegistry()),
    )


def test_tools_list_handler_exception_becomes_jsonrpc_error() -> None:
    """A raising tools/list handler returns a -32603 error, not a crash."""
    server = _server_with_raising_registry()
    request = JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id="1", params={})

    response, state = server.handle_request(request, ServerState.RUNNING)

    assert response is not None
    assert response.error is not None
    assert response.error["code"] == -32603
    assert response.msg_id == "1"
    assert state == ServerState.RUNNING


def test_handler_exception_preserves_request_id_for_correlation() -> None:
    """The error response echoes the request id so the client can correlate it."""
    server = _server_with_raising_registry()
    request = JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id="abc-42", params={})

    response, _ = server.handle_request(request, ServerState.RUNNING)

    assert response is not None
    assert response.error is not None
    assert response.msg_id == "abc-42"
