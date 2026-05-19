"""Minimal legacy HTTP+SSE MCP server fixture.

Exposes a legacy `/sse` endpoint that emits an `endpoint` event naming a
message POST endpoint. Client JSON-RPC responses are delivered back over the
open SSE stream as `message` events so tests exercise the full legacy flow.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import queue
    from collections.abc import Mapping
from tests.fixtures.fake_sse_mcp_helper__mcphandler import _McpHandler
from tests.fixtures.fake_sse_mcp_helper__sessionregistry import _SessionRegistry

_PROTOCOL_VERSION = "2024-11-05"



@dataclass
class _SessionState:
    events: queue.Queue[bytes]






_SESSIONS = _SessionRegistry()


def _message_event(payload: Mapping[str, object]) -> bytes:
    body = json.dumps(payload)
    return f"event: message\ndata: {body}\n\n".encode()


def _endpoint_event(endpoint: str) -> bytes:
    return f"event: endpoint\ndata: {endpoint}\n\n".encode()


def _error_payload(req_id: object, code: int, message: str) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _tool_name(params: object) -> str | None:
    if not isinstance(params, dict):
        return None
    name = params.get("name")
    return name if isinstance(name, str) else None


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _McpHandler)
    port = server.server_address[1]
    print(port, flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
