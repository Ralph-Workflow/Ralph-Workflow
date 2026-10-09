"""A broken activity relay must be visible to the supervisor's MCP probe."""

from __future__ import annotations

import pytest

from ralph.mcp.protocol._permanent_preflight_error import PermanentPreflightError
from ralph.mcp.protocol._startup_http import ensure_no_preflight_error
from ralph.mcp.server._activity_relay_error import ActivityRelayError
from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._mcp_server import McpServer
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.tools.bridge import ToolBridge

_FAULT = "SUPERVISION_INFRASTRUCTURE_FAILURE: activity relay sender: timed out"


def test_relay_fault_is_reported_by_control_requests_without_more_tool_calls() -> None:
    def failed_relay(_tool_name: str) -> None:
        raise ActivityRelayError(_FAULT)

    server = McpServer(
        session=object(),
        workspace=object(),
        registry=ToolBridge(),
        mcp_activity_sink=failed_relay,
    )
    failed_call, _ = server.handle_request(
        JsonRpcRequest(
            jsonrpc="2.0",
            method="tools/call",
            msg_id=1,
            params={"name": "read_file", "arguments": {}},
        ),
        ServerState.RUNNING,
    )
    assert failed_call is not None
    assert failed_call.error == {"code": -32070, "message": _FAULT}

    for method in ("initialize", "tools/list"):
        response, state = server.handle_request(
            JsonRpcRequest(jsonrpc="2.0", method=method, msg_id=2, params={}),
            ServerState.RUNNING,
        )
        assert response is not None
        assert response.error == failed_call.error
        assert response.msg_id == 2
        assert state is ServerState.RUNNING
        with pytest.raises(PermanentPreflightError, match="SUPERVISION_INFRASTRUCTURE_FAILURE"):
            ensure_no_preflight_error("supervisor probe", response.error)


def test_healthy_control_requests_do_not_emit_activity() -> None:
    observed: list[str] = []
    server = McpServer(
        session=object(),
        workspace=object(),
        registry=ToolBridge(),
        mcp_activity_sink=observed.append,
    )
    response, _ = server.handle_request(
        JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id=1, params={}),
        ServerState.RUNNING,
    )
    assert response is not None
    assert response.error is None
    assert response.result == {"tools": []}
    assert observed == []
