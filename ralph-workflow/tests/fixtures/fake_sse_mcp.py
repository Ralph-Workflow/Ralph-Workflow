"""Minimal legacy HTTP+SSE MCP server fixture.

Exposes a legacy `/sse` endpoint that emits an `endpoint` event naming a
message POST endpoint. Client JSON-RPC responses are delivered back over the
open SSE stream as `message` events so tests exercise the full legacy flow.
"""

from __future__ import annotations

import json
import queue
import sys
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

if TYPE_CHECKING:
    from collections.abc import Mapping

_PROTOCOL_VERSION = "2024-11-05"


@dataclass
class _SessionState:
    events: queue.Queue[bytes]


class _SessionRegistry:
    def __init__(self) -> None:
        self._sessions: dict[str, _SessionState] = {}
        self._lock = Lock()

    def create(self) -> tuple[str, _SessionState]:
        session_id = uuid.uuid4().hex
        state = _SessionState(events=queue.Queue())
        with self._lock:
            self._sessions[session_id] = state
        return session_id, state

    def get(self, session_id: str) -> _SessionState | None:
        with self._lock:
            return self._sessions.get(session_id)


class _McpHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/sse":
            self.send_error(HTTPStatus.NOT_FOUND.value)
            return
        session_id, state = _SESSIONS.create()
        message_endpoint = f"/message?sessionId={session_id}"
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.wfile.write(_endpoint_event(message_endpoint))
        self.wfile.flush()
        while True:
            try:
                payload = state.events.get(timeout=30)
            except queue.Empty:
                return
            self.wfile.write(payload)
            self.wfile.flush()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/message":
            self.send_error(HTTPStatus.NOT_FOUND.value)
            return
        session_id = parse_qs(parsed.query).get("sessionId", [""])[0]
        state = _SESSIONS.get(session_id)
        if state is None:
            self.send_error(HTTPStatus.NOT_FOUND.value)
            return
        payload = self._load_payload()
        if payload is None:
            return
        req_id = payload.get("id")
        method = payload.get("method")
        event_payload: Mapping[str, object] | None = None
        if method == "initialize":
            event_payload = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "serverInfo": {"name": "fake-sse-mcp", "version": "0.1.0"},
                    "capabilities": {},
                },
            }
        elif method == "notifications/initialized":
            event_payload = None
        elif method == "tools/list":
            event_payload = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": [
                        {
                            "name": "fake_tool",
                            "description": "A fake legacy SSE MCP tool for testing",
                            "inputSchema": {"type": "object", "properties": {}},
                        }
                    ]
                },
            }
        elif method == "tools/call":
            tool_name = _tool_name(payload.get("params"))
            if tool_name == "fake_tool":
                event_payload = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": "fake-sse-result"}],
                        "isError": False,
                    },
                }
            else:
                event_payload = _error_payload(req_id, -32602, f"Unknown tool: {tool_name}")
        else:
            event_payload = _error_payload(req_id, -32601, f"Method not found: {method}")
        if event_payload is not None:
            state.events.put(_message_event(event_payload))
        self._send_accepted()

    def _load_payload(self) -> dict[str, object] | None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send_json(HTTPStatus.BAD_REQUEST, _error_payload(None, -32700, "parse error"))
            return None
        if not isinstance(payload, dict):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                _error_payload(None, -32600, "invalid request"),
            )
            return None
        return payload

    def _send_json(self, status: HTTPStatus, payload: Mapping[str, object]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_accepted(self) -> None:
        self.send_response(HTTPStatus.ACCEPTED.value)
        self.send_header("Content-Length", "0")
        self.end_headers()


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
