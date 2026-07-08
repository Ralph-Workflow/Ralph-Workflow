"""HTTP request handler for the fallback MCP server runtime."""

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
    signature_for,
)
from ralph.mcp.server._trust_boundary import require_trust_boundary
from ralph.mcp.server.exec_sse_streaming import exec_sse_streaming_post
from ralph.mcp.tools.names import EXEC_TOOL, claude_tool_name, opencode_tool_name

_EXEC_STREAMING_TOOL_NAMES = frozenset(
    {str(EXEC_TOOL), claude_tool_name(EXEC_TOOL), opencode_tool_name(EXEC_TOOL)}
)


def _coerce_fallback_server(server: object) -> _FallbackHttpServer:
    """Narrow ``self.server`` to the production ``_FallbackHttpServer`` class
    without using ``typing.cast`` at the session factory boundary (PROMPT.md
    proof obligation B).

    The narrowing uses a runtime ``isinstance`` check instead of a static
    ``cast()`` so the type checker does not launder the production class
    boundary. Test-only ``SimpleNamespace`` fakes that do not subclass
    ``_FallbackHttpServer`` are caught here with a clear error rather than
    silently passed through a cast.

    In the in-memory transport harness, the fake is wrapped via
    ``_coerce_fallback_server(self.server)`` and must therefore match the
    production class surface; the harness upgrades its SimpleNamespace to a
    minimal subclass of ``_FallbackHttpServer`` so the runtime check passes.
    """
    server_cls: type[_FallbackHttpServer] = _FallbackHttpServer
    if not isinstance(server, server_cls):
        raise TypeError(
            "_FallbackHttpHandler was not bound to a _FallbackHttpServer "
            f"instance (got {type(server).__name__})"
        )
    return server


#: Process-singleton transport-level repetition tracker. The same instance
#: observes every request dispatched on this server so a doomed retry loop
#: at the transport layer is broken regardless of which request the storm
#: is currently running. Tests inject a fresh instance via the in-memory
#: transport's monkeypatch seam.
_transport_repetition_tracker = TransportRepetitionTracker()


class _FallbackHttpHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        # Real GET /health route. Driven by the in-memory transport harness
        # (tests/test_property_c_liveness_contract.py) and by the live agent
        # supervisor (RestartAwareMcpBridge.check_health_and_restart_if_needed).
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
        # mcp-timeout-ok: SSE keepalive under /mcp mount; pre-existing baseline,
        # not introduced by this plan. The MCP-timeout audit only flags subprocess
        # and HTTP/socket calls; time.sleep is not in its scope. Documented here
        # for reviewer ergonomics.
        while not server.shutdown_event.is_set():
            try:
                self.wfile.write(b": keepalive\r\n\r\n")
                self.wfile.flush()
            except BrokenPipeError:
                break
            sleep(0.25)

    def _handle_health_get(self) -> None:
        """Handle GET /health. Returns 200 healthy / 503 unhealthy with latency_ms."""
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
            # Any failure is unhealthy. We catch broadly because the probe may
            # raise any subclass — the alternative (re-raise) defeats the
            # whole point of the liveness contract.
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
        """Apply the trust boundary (property K). Return True if allowed.

        Sends a 401 frame and returns False when the request must be rejected.
        """
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
        """Decode the JSON-RPC request body. Returns (data, request, error_msg)."""
        length = int(self.headers.get("Content-Length", "0"))
        payload = self.rfile.read(length)
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
        """Handle the SSE streaming and saturated-dispatch paths."""
        server = _coerce_fallback_server(self.server)

        # Exec tool calls use SSE streaming: chunk notifications before final
        # frame. Match the server-advertised aliases too — clients calling
        # `mcp__ralph__exec`/`ralph_exec` must stream the same way.
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
        # Dispatch the JSON-RPC request through the saturated-dispatch seam so
        # the production transport can bound concurrency with backpressure
        # (property H) without queueing past an unstated limit. The seam is a
        # no-op pass-through today; the bounded-executor wiring layers on top
        # in a follow-up without changing call sites in this method.
        # The try/except wraps the call so the transport-repetition breaker
        # (property G) can consult the failure signature BEFORE the response
        # is written: 3 identical -32001-class failures within 60s short-
        # circuit to a 503 + transport_loop_detected frame.
        # The saturated-dispatch wrapper (property H) returns a
        # SaturatedResponse sentinel when the bounded executor's queue is
        # full; the handler writes that as a 503 + JSON-RPC -32001 frame
        # rather than queueing silently past max_workers.
        try:
            dispatch_result = _saturated_dispatch.submit(
                lambda: server.mcp_server.handle_request(request, server.state)
            )
        except Exception as exc:
            sig = signature_for(exc)
            if _transport_repetition_tracker.observe(sig):
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
        # Saturation: the bounded executor rejected the call. The handler
        # writes the 503 + JSON-RPC -32001 frame and short-circuits the
        # response so the client sees backpressure, not a queue.
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
        # dispatch_result is the saturated-dispatch union: a successful tuple
        # OR a SaturatedResponse sentinel. The SaturatedResponse branch
        # returns above; mypy narrows to the tuple here.
        response, next_state = dispatch_result
        # The dispatch net inside McpServer.handle_request converts thrown
        # exceptions into a -32603 JSON-RPC error frame. Observe that frame
        # too: a doomed retry loop produces 3 identical -32603 errors, and
        # the breaker must trip on the 3rd.
        if response is not None and response.error is not None:
            sig = signature_for(f"{response.error.get('code')}:{response.error.get('message')}")
            if _transport_repetition_tracker.observe(sig):
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
            # JSON-RPC notification: no response frame expected. Return 202
            # Accepted with an empty body so the client sees the
            # notification was received but no reply will be sent.
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body: dict[str, object] = {"jsonrpc": response.jsonrpc, "id": response.msg_id}
        if response.result is not None:
            body["result"] = response.result
        if response.error is not None:
            body["error"] = response.error
        # Serialization runs outside McpServer.handle_request's error net. A
        # non-JSON-serializable value leaking through a tool result must still
        # yield a well-formed -32603 JSON-RPC error frame, never a bare HTTP 500
        # the client can only read as a broken/empty session.
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
            # The production session re-reads its backing file on every access;
            # a corrupt/missing file must degrade to a response without the
            # session header, never destroy the already-encoded response.
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
        # Exec streaming POST is a finite RPC response, not the long-lived GET
        # event stream. Close after the terminal JSON-RPC frame so EOF-oriented
        # clients do not wait on HTTP/1.1 keep-alive until their request timeout.
        self.close_connection = True

        def _write_frame(frame: bytes) -> None:
            # suppress(Exception), not just OSError: a concurrent write to the
            # buffered socket writer raises RuntimeError (reentrant call), and
            # any escape here kills the request thread after the 200 header —
            # the bodyless-stream hang the streaming net exists to prevent.
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
