"""HTTP request handler for the fallback MCP server runtime."""

from __future__ import annotations

import json
from contextlib import suppress
from http.server import BaseHTTPRequestHandler
from time import sleep
from typing import TYPE_CHECKING, cast

from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._runtime_constants import DEFAULT_MOUNT_PATH

if TYPE_CHECKING:
    from ralph.mcp.server._fallback_http_server import _FallbackHttpServer


class _FallbackHttpHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if self.path != DEFAULT_MOUNT_PATH:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.wfile.write(b"event: open\r\ndata: {}\r\n\r\n")
        self.wfile.flush()
        server = cast("_FallbackHttpServer", self.server)
        while not server.shutdown_event.is_set():
            try:
                self.wfile.write(b": keepalive\r\n\r\n")
                self.wfile.flush()
            except BrokenPipeError:
                break
            sleep(0.25)

    def do_POST(self) -> None:
        if self.path != DEFAULT_MOUNT_PATH:
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length)
        try:
            data = cast("dict[str, object]", json.loads(payload or b"{}"))
        except json.JSONDecodeError:
            self._write_json(
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32700, "message": "Parse error"},
                    "id": None,
                },
                400,
            )
            return
        params_value = data.get("params")
        request = JsonRpcRequest(
            jsonrpc=cast("str", data.get("jsonrpc", "2.0")),
            method=cast("str", data.get("method", "")),
            params=cast("dict[str, object] | None", params_value)
            if isinstance(params_value, dict)
            else None,
            msg_id=data.get("id"),
        )
        server = cast("_FallbackHttpServer", self.server)

        # Exec tool calls use SSE streaming: chunk notifications before final frame.
        if (
            request.method == "tools/call"
            and isinstance(request.params, dict)
            and request.params.get("name") == "exec"
        ):
            self._handle_exec_streaming_post(request, server)
            return

        response, next_state = server.mcp_server.handle_request(request, server.state)
        server.state = next_state
        if response is None:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body: dict[str, object] = {"jsonrpc": response.jsonrpc, "id": response.msg_id}
        if response.result is not None:
            body["result"] = response.result
        if response.error is not None:
            body["error"] = response.error
        encoded = f"event: message\r\ndata: {json.dumps(body)}\r\n\r\n".encode()
        session_id = None
        if request.method == "initialize":
            session_id = cast("_FallbackHttpServer", self.server).mcp_server._session.session_id
        self._write_sse(encoded, 200, session_id=session_id)

    def _handle_exec_streaming_post(
        self,
        request: JsonRpcRequest,
        server: _FallbackHttpServer,
    ) -> None:
        """Handle a tools/call exec request with SSE chunk notification streaming.

        Sends text/event-stream headers (no Content-Length) before dispatch so
        chunks can be flushed immediately. After dispatch the final tools/call
        response frame is written. AgentSession.tool_output_sink is restored in
        finally so subsequent calls are not affected.
        """
        session = server.mcp_server._session
        previous_sink = session.tool_output_sink

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        def _write_notification(event: dict[str, object]) -> None:
            notification: dict[str, object] = {
                "jsonrpc": "2.0",
                "method": "notifications/message",
                "params": event,
            }
            frame = f"event: message\r\ndata: {json.dumps(notification)}\r\n\r\n".encode()
            with suppress(OSError):
                self.wfile.write(frame)
                self.wfile.flush()

        session.tool_output_sink = _write_notification
        try:
            response, next_state = server.mcp_server.handle_request(request, server.state)
            server.state = next_state
            if response is not None:
                body: dict[str, object] = {"jsonrpc": response.jsonrpc, "id": response.msg_id}
                if response.result is not None:
                    body["result"] = response.result
                if response.error is not None:
                    body["error"] = response.error
                final_frame = f"event: message\r\ndata: {json.dumps(body)}\r\n\r\n".encode()
                with suppress(OSError):
                    self.wfile.write(final_frame)
                    self.wfile.flush()
        except Exception as exc:
            error_body: dict[str, object] = {
                "jsonrpc": "2.0",
                "id": request.msg_id,
                "error": {"code": -32603, "message": str(exc)},
            }
            err_frame = f"event: message\r\ndata: {json.dumps(error_body)}\r\n\r\n".encode()
            with suppress(OSError):
                self.wfile.write(err_frame)
                self.wfile.flush()
        finally:
            session.tool_output_sink = previous_sink

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _write_json(self, payload: dict[str, object], status: int) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _write_sse(self, payload: bytes, status: int, *, session_id: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        if session_id:
            self.send_header("mcp-session-id", session_id)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
