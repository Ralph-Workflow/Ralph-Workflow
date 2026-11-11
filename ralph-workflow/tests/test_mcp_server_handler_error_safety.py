from __future__ import annotations

from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._mcp_server import McpServer
from ralph.mcp.server._server_state import ServerState


class _RaisingRegistry:
    def list_definitions(self) -> list[object]:
        raise RuntimeError("kaboom enumerating tools")


def _server_with_raising_registry() -> McpServer:
    return McpServer(
        session=object(),
        workspace=object(),
        registry=_RaisingRegistry(),
    )


def test_tools_list_handler_exception_becomes_jsonrpc_error() -> None:
    server = _server_with_raising_registry()
    request = JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id="1", params={})

    response, state = server.handle_request(request, ServerState.RUNNING)

    assert response is not None
    assert response.error is not None
    assert response.error["code"] == -32603
    assert response.msg_id == "1"
    assert state == ServerState.RUNNING


def test_handler_exception_preserves_request_id_for_correlation() -> None:
    server = _server_with_raising_registry()
    request = JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id="abc-42", params={})

    response, _ = server.handle_request(request, ServerState.RUNNING)

    assert response is not None
    assert response.error is not None
    assert response.msg_id == "abc-42"
