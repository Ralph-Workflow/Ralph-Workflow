"""In-process MCP test harness.

Shared deterministic fakes for the MCP JSON-RPC protocol. Tests use these
to exercise upstream client and session logic without TCP ports or subprocesses.
"""

from __future__ import annotations

import json
import uuid
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import httpx

from ralph.mcp.upstream.models import UpstreamCallError, UpstreamTool

if TYPE_CHECKING:
    from ralph.mcp.upstream.config import UpstreamMcpServer

_PROTOCOL_VERSION = "2024-11-05"

FAKE_TOOL = UpstreamTool(
    name="fake_tool",
    description="A fake MCP tool for testing",
)

HTTP_CALL_RESULT: dict[str, Any] = {
    "content": [{"type": "text", "text": "fake-http-result"}],
    "isError": False,
}

SSE_CALL_RESULT: dict[str, Any] = {
    "content": [{"type": "text", "text": "fake-sse-result"}],
    "isError": False,
}


class StubUpstreamClient:
    """In-process upstream client for fast deterministic tests."""

    def __init__(
        self,
        tools: list[UpstreamTool] | None = None,
        call_result: dict[str, Any] | None = None,
    ) -> None:
        self._tools = tools if tools is not None else [FAKE_TOOL]
        self._call_result = call_result or HTTP_CALL_RESULT

    def list_tools(self) -> list[UpstreamTool]:
        return list(self._tools)

    def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        del arguments
        if name in {t.name for t in self._tools}:
            return dict(self._call_result)
        raise UpstreamCallError(f"Unknown tool: {name}")


def make_stub_client_factory(
    tools: list[UpstreamTool] | None = None,
    call_result: dict[str, Any] | None = None,
):
    """Return a client_factory suitable for UpstreamRegistry.build()."""
    _tools = tools
    _result = call_result

    def factory(server: UpstreamMcpServer) -> StubUpstreamClient:
        del server
        return StubUpstreamClient(tools=_tools, call_result=_result)

    return factory


def make_fake_http_post_fn(server_name: str = "fake-http-mcp"):
    """Return a post_fn for post_http_jsonrpc_with_session that emulates _McpHandler.

    Implements the MCP JSON-RPC response protocol in-process: no TCP port,
    no subprocess, no sleep. The session_id is generated once per factory
    call and reused across all requests within the same session.
    """
    _json = json  # capture before 'json' kwarg shadows the module in the inner function
    _session_id = uuid.uuid4().hex

    def fake_post(
        url: str,
        *,
        json: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        del url, timeout, headers
        method = json.get("method", "")
        req_id = json.get("id")

        if method == "initialize":
            body = _json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": _PROTOCOL_VERSION,
                        "serverInfo": {"name": server_name, "version": "0.1.0"},
                        "capabilities": {},
                    },
                }
            )
            return httpx.Response(
                HTTPStatus.OK.value,
                content=body,
                headers={"mcp-session-id": _session_id, "Content-Type": "application/json"},
            )

        if method == "notifications/initialized":
            return httpx.Response(
                HTTPStatus.ACCEPTED.value,
                content=b"",
                headers={"mcp-session-id": _session_id},
            )

        if method == "tools/list":
            body = _json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "tools": [
                            {
                                "name": "fake_tool",
                                "description": "A fake HTTP MCP tool for testing",
                                "inputSchema": {"type": "object", "properties": {}},
                            }
                        ]
                    },
                }
            )
            return httpx.Response(
                HTTPStatus.OK.value,
                content=body,
                headers={"mcp-session-id": _session_id, "Content-Type": "application/json"},
            )

        if method == "tools/call":
            params = json.get("params", {})
            tool_name = params.get("name") if isinstance(params, dict) else None
            if tool_name == "fake_tool":
                body = _json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": HTTP_CALL_RESULT,
                    }
                )
            else:
                body = _json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32602, "message": f"Unknown tool: {tool_name}"},
                    }
                )
            return httpx.Response(
                HTTPStatus.OK.value,
                content=body,
                headers={"mcp-session-id": _session_id, "Content-Type": "application/json"},
            )

        body = _json.dumps(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        )
        return httpx.Response(
            HTTPStatus.OK.value,
            content=body,
            headers={"mcp-session-id": _session_id, "Content-Type": "application/json"},
        )

    return fake_post
