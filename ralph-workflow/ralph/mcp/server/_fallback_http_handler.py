from __future__ import annotations

import json
import os
from contextlib import suppress
from http.server import BaseHTTPRequestHandler
from time import sleep

from ralph.mcp.server import _saturated_dispatch
from ralph.mcp.server._fallback_http_server import _FallbackHttpServer
from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._metrics import get_default_metrics
from ralph.mcp.server._runtime_constants import DEFAULT_MOUNT_PATH
from ralph.mcp.server._transport_repetition_tracker import (
    TransportRepetitionTracker,
    failure_signature,
)
from ralph.mcp.server._trust_boundary import require_trust_boundary
from ralph.mcp.server.exec_sse_streaming import exec_sse_streaming_post
from ralph.mcp.tools.names import (
    EXEC_TOOL,
    RAW_EXEC_TOOL,
    UNSAFE_EXEC_TOOL,
    claude_tool_name,
    opencode_tool_name,
)

_EXEC_STREAMING_BASE_NAMES: tuple[str, ...] = (
    str(EXEC_TOOL),
    str(UNSAFE_EXEC_TOOL),
    str(RAW_EXEC_TOOL),
    "exec_wait",
)

_EXEC_STREAMING_TOOL_NAMES = frozenset(
    name
    for base in _EXEC_STREAMING_BASE_NAMES
    for name in (base, claude_tool_name(base), opencode_tool_name(base))
)


def _coerce_fallback_server(server: object) -> _FallbackHttpServer:
    server_cls: type[_FallbackHttpServer] = _FallbackHttpServer
    if not isinstance(server, server_cls):
        raise TypeError(
            "_FallbackHttpHandler was not bound to a _FallbackHttpServer "
            f"instance (got {type(server).__name__})"
        )
    return server


_transport_repetition_tracker = TransportRepetitionTracker.from_parent_owned_state()


class _FallbackHttpHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        if self.path == "/health":
            self._handle_health_get()
            return
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
        server = _coerce_fallback_server(self.server)
        while not server.shutdown_event.is_set():
            try:
                self.wfile.write(b": keepalive\r\n\r\n")
                self.wfile.flush()
            except BrokenPipeError:
                break
            sleep(0.25)

    def _handle_health_get(self) -> None:
        server = _coerce_fallback_server(self.server)
        probe = server.health_probe_fn
        metrics = server.metrics or get_default_metrics()
        if probe is None:
            self._write_json(
                {"status": "healthy", "latency_ms": 0.0, "note": "no probe configured"},
                200,
            )
            return
        try:
            result = probe()
        except Exception as exc:
            metrics.record_health_probe_outcome(False)
            self._write_json(
                {"status": "unhealthy", "reason": type(exc).__name__},
                503,
            )
            return
        metrics.record_health_probe_outcome(bool(result.healthy))
        if bool(result.healthy):
            self._write_json(
                {
                    "status": "healthy",
                    "latency_ms": float(result.latency_ms),
                },
                200,
            )
            return
        self._write_json(
            {
                "status": "unhealthy",
                "reason": str(result.reason or "probe_failed"),
            },
            503,
        )

    def do_POST(self) -> None:
        if self.path != DEFAULT_MOUNT_PATH:
            self.send_error(404)
            return
        if not self._authorize_request():
            return
        data, request, error_message = self._parse_request_body()
        if data is None or request is None:
            self._write_json(
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32700, "message": error_message or "Parse error"},
                    "id": None,
                },
                400,
            )
            return
        self._dispatch_parsed_request(request, data)

    def _authorize_request(self) -> bool:
        try:
            require_trust_boundary(
                self.headers.get("Authorization"),
                os.environ,
            )
        except PermissionError:
            self._write_json(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32001, "message": "unauthorized"},
                },
                401,
            )
            return False
        return True

    def _parse_request_body(
        self,
    ) -> tuple[dict[str, object] | None, JsonRpcRequest | None, str | None]:
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length)
        if len(payload) != length:
            return (
                None,
                None,
                (
                    f"Parse error: truncated request body (expected {length} bytes, received {len(payload)})"
                ),
            )
        try:
            decoded: object = json.loads(payload or b"{}")
        except json.JSONDecodeError as exc:
            return None, None, f"Parse error: {exc}"
        if not isinstance(decoded, dict):
            return None, None, "Parse error: expected object"
        data_obj: dict[str, object] = decoded
        params_value: object = data_obj.get("params")
        jsonrpc_value: object = data_obj.get("jsonrpc", "2.0")
        method_value: object = data_obj.get("method", "")
        params_param: dict[str, object] | None = (
            params_value if isinstance(params_value, dict) else None
        )
        request = JsonRpcRequest(
            jsonrpc=jsonrpc_value if isinstance(jsonrpc_value, str) else "2.0",
            method=method_value if isinstance(method_value, str) else "",
            params=params_param,
            msg_id=data_obj.get("id"),
        )
        return data_obj, request, None

    def _dispatch_parsed_request(
        self,
        request: JsonRpcRequest,
        data: dict[str, object],
    ) -> None:
        server = _coerce_fallback_server(self.server)

        if (
            request.method == "tools/call"
            and isinstance(request.params, dict)
            and request.params.get("name") in _EXEC_STREAMING_TOOL_NAMES
        ):
            self._handle_exec_streaming_post(request, server)
            return
        self._dispatch_standard_request(request, server, data)

    def _dispatch_standard_request(
        self,
        request: JsonRpcRequest,
        server: _FallbackHttpServer,
        data: dict[str, object],
    ) -> None:
        try:
            dispatch_result = _saturated_dispatch.submit(
                lambda: server.mcp_server.handle_request(request, server.state)
            )
        except Exception as exc:
            sig = failure_signature(request.method, request.params, exc)
            if sig is not None and _transport_repetition_tracker.observe(sig):
                self._write_json(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": -32001,
                            "message": "transport_loop_detected",
                        },
                    },
                    503,
                )
                return
            raise
        if isinstance(dispatch_result, _saturated_dispatch.SaturatedResponse):
            self._write_json(
                {
                    "jsonrpc": "2.0",
                    "id": request.msg_id,
                    "error": {
                        "code": dispatch_result.code,
                        "message": dispatch_result.message,
                    },
                },
                _saturated_dispatch.SATURATION_STATUS,
            )
            return
        response, next_state = dispatch_result
        if response is not None and response.error is not None:
            sig = failure_signature(
                request.method,
                request.params,
                f"{response.error.get('code')}:{response.error.get('message')}",
            )
            if sig is not None and _transport_repetition_tracker.observe(sig):
                self._write_json(
                    {
                        "jsonrpc": "2.0",
                        "id": response.msg_id,
                        "error": {
                            "code": -32001,
                            "message": "transport_loop_detected",
                        },
                    },
                    503,
                )
                return
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
        try:
            encoded = f"event: message\r\ndata: {json.dumps(body)}\r\n\r\n".encode()
        except (TypeError, ValueError) as exc:
            error_body: dict[str, object] = {
                "jsonrpc": "2.0",
                "id": response.msg_id,
                "error": {
                    "code": -32603,
                    "message": f"Response serialization failed: {exc}",
                },
            }
            encoded = f"event: message\r\ndata: {json.dumps(error_body)}\r\n\r\n".encode()
        session_id = None
        if request.method == "initialize":
            with suppress(Exception):
                session_id = _coerce_fallback_server(self.server).mcp_server._session.session_id
        self._write_sse(encoded, 200, session_id=session_id)

    def _handle_exec_streaming_post(
        self,
        request: JsonRpcRequest,
        server: _FallbackHttpServer,
    ) -> None:
        session = server.mcp_server._session

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def _write_frame(frame: bytes) -> None:
            with suppress(Exception):
                self.wfile.write(frame)
                self.wfile.flush()

        server.state = exec_sse_streaming_post(
            request,
            session,
            server.mcp_server.handle_request,
            server.state,
            write_frame=_write_frame,
        )

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
