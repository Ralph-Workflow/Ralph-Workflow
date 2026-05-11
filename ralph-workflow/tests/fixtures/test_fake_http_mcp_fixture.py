"""Tests for the HTTP MCP session protocol using the in-process harness."""

from __future__ import annotations

from ralph.mcp.protocol.startup import (
    initialize_request,
    initialized_notification,
    parse_http_endpoint,
    post_http_jsonrpc_with_session,
    tools_list_request,
)
from tests.fixtures.mcp_test_harness import make_fake_http_post_fn

_ENDPOINT = "http://127.0.0.1:9999/mcp"
_TARGET = parse_http_endpoint(_ENDPOINT)


class TestHttpMcpSessionHandshake:
    def test_initialize_returns_session_id_and_server_info(self) -> None:
        post_fn = make_fake_http_post_fn("fake-http-mcp")

        response, session_id = post_http_jsonrpc_with_session(
            _ENDPOINT, _TARGET, initialize_request(), post_fn=post_fn
        )

        assert session_id
        assert response["id"] == 1
        result = response["result"]
        assert isinstance(result, dict)
        server_info = result["serverInfo"]
        assert isinstance(server_info, dict)
        assert server_info["name"] == "fake-http-mcp"

    def test_tools_list_after_handshake_returns_expected_definitions(self) -> None:
        post_fn = make_fake_http_post_fn()

        _, session_id = post_http_jsonrpc_with_session(
            _ENDPOINT, _TARGET, initialize_request(), post_fn=post_fn
        )
        post_http_jsonrpc_with_session(
            _ENDPOINT,
            _TARGET,
            initialized_notification(),
            session_id=session_id,
            post_fn=post_fn,
        )
        tools_response, _ = post_http_jsonrpc_with_session(
            _ENDPOINT,
            _TARGET,
            tools_list_request(),
            session_id=session_id,
            post_fn=post_fn,
        )

        tools_result = tools_response["result"]
        assert isinstance(tools_result, dict)
        tools = tools_result["tools"]
        assert isinstance(tools, list)
        assert len(tools) == 1
        first = tools[0]
        assert isinstance(first, dict)
        assert first["name"] == "fake_tool"
