"""Minimal single-threaded HTTP MCP server fixture.

Mirrors ``fake_stdio_mcp.py`` but speaks the 2024-11-05 streamable-http
variant of MCP. The handler is intentionally single-threaded so upstream
tests can assume sequential JSON-RPC calls; do not pipeline requests
against this fixture. Sessions are tracked but stateless requests are
accepted so Ralph's session-less ``HttpUpstreamClient`` can still probe
``tools/list`` without first doing ``initialize``.
"""

from __future__ import annotations

import json
import sys
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

_PROTOCOL_VERSION = "2024-11-05"


class _McpHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send_parse_error()
            return

        if not isinstance(payload, dict):
            self._send_invalid_request()
            return

        method = payload.get("method", "")
        req_id = payload.get("id")
        session_id = self.headers.get("mcp-session-id")

        dispatch = {
            "initialize": self._handle_initialize,
            "notifications/initialized": self._handle_initialized_notification,
            "tools/list": self._handle_tools_list,
            "tools/call": self._handle_tools_call,
        }
        handler = dispatch.get(method)
        if handler is None:
            self._send_method_not_found(req_id, method, session_id)
            return
        handler(req_id, payload, session_id)

    def _handle_initialize(
        self, req_id: object, payload: dict[str, object], session_id: str | None
    ) -> None:
        del payload, session_id
        new_session = uuid.uuid4().hex
        self._send_json(
            HTTPStatus.OK,
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "serverInfo": {"name": "fake-http-mcp", "version": "0.1.0"},
                    "capabilities": {},
                },
            },
            session_id=new_session,
        )

    def _handle_initialized_notification(
        self, req_id: object, payload: dict[str, object], session_id: str | None
    ) -> None:
        del req_id, payload
        self._send_accepted(session_id=session_id)

    def _handle_tools_list(
        self, req_id: object, payload: dict[str, object], session_id: str | None
    ) -> None:
        del payload
        self._send_json(
            HTTPStatus.OK,
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
            },
            session_id=session_id,
        )

    def _handle_tools_call(
        self, req_id: object, payload: dict[str, object], session_id: str | None
    ) -> None:
        tool_name = _tool_name(payload.get("params"))
        if tool_name == "fake_tool":
            self._send_json(
                HTTPStatus.OK,
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": "fake-http-result"}],
                        "isError": False,
                    },
                },
                session_id=session_id,
            )
            return
        self._send_json(
            HTTPStatus.OK,
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": f"Unknown tool: {tool_name}"},
            },
            session_id=session_id,
        )

    def _send_method_not_found(self, req_id: object, method: str, session_id: str | None) -> None:
        self._send_json(
            HTTPStatus.OK,
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            },
            session_id=session_id,
        )

    def _send_parse_error(self) -> None:
        self._send_json(
            HTTPStatus.BAD_REQUEST,
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "parse error"},
            },
        )

    def _send_invalid_request(self) -> None:
        self._send_json(
            HTTPStatus.BAD_REQUEST,
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "invalid request"},
            },
        )

    def _send_json(
        self,
        status: HTTPStatus,
        payload: Mapping[str, object],
        *,
        session_id: str | None = None,
    ) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if session_id:
            self.send_header("mcp-session-id", session_id)
        self.end_headers()
        self.wfile.write(body)

    def _send_accepted(self, *, session_id: str | None) -> None:
        self.send_response(HTTPStatus.ACCEPTED.value)
        self.send_header("Content-Length", "0")
        if session_id:
            self.send_header("mcp-session-id", session_id)
        self.end_headers()


def _tool_name(params: object) -> str | None:
    if not isinstance(params, dict):
        return None
    name = params.get("name")
    return name if isinstance(name, str) else None


def main() -> None:
    server = HTTPServer(("127.0.0.1", 0), _McpHandler)
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
