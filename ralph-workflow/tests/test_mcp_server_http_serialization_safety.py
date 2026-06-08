"""Regression test: the HTTP handler must not crash on a non-serializable result.

`McpServer.handle_request` now converts handler exceptions to JSON-RPC errors,
but `do_POST` serializes the response with `json.dumps` AFTER that call returns.
If a tool result leaks a non-JSON-serializable value (bytes/set/Path/datetime
through a to_dict()/model_dump()), the `json.dumps` raised OUTSIDE the net and
produced a bare HTTP 500 with no JSON-RPC body — the same broken-session failure
class. This pins that the non-streaming POST path converts a serialization
failure into a -32603 JSON-RPC error frame instead.
"""

from __future__ import annotations

import io
import json
from email.message import Message

from ralph.mcp.server._fallback_http_handler import _FallbackHttpHandler
from ralph.mcp.server._json_rpc_response import JsonRpcResponse
from ralph.mcp.server._runtime_constants import DEFAULT_MOUNT_PATH
from ralph.mcp.server._server_state import ServerState


class _NonSerializableMcp:
    def handle_request(
        self, request: object, state: object
    ) -> tuple[JsonRpcResponse, object]:
        # A set is not JSON-serializable; it passes through handle_request
        # (a valid JsonRpcResponse) and only fails at json.dumps in do_POST.
        msg_id = getattr(request, "msg_id", None)
        return (
            JsonRpcResponse(jsonrpc="2.0", result={"tools": {1, 2, 3}}, msg_id=msg_id),
            state,
        )


class _Server:
    def __init__(self) -> None:
        self.mcp_server = _NonSerializableMcp()
        self.state: object = ServerState.RUNNING


def test_do_post_non_serializable_result_emits_jsonrpc_error_not_crash() -> None:
    payload = json.dumps(
        {"jsonrpc": "2.0", "method": "tools/list", "id": "req-1"}
    ).encode("utf-8")

    headers = Message()
    headers["Content-Length"] = str(len(payload))
    wfile = io.BytesIO()

    handler = object.__new__(_FallbackHttpHandler)
    handler.path = DEFAULT_MOUNT_PATH
    handler.headers = headers
    handler.rfile = io.BytesIO(payload)
    handler.wfile = wfile
    handler.send_response = lambda code: None
    handler.send_header = lambda name, value: None
    handler.end_headers = lambda: None
    handler.server = _Server()

    handler.do_POST()  # must not raise

    raw = wfile.getvalue().decode("utf-8")
    assert "-32603" in raw
    assert "req-1" in raw
