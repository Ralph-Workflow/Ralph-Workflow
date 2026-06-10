"""Regression tests: fallback handler must survive session-file failures and route aliases.

Two hardening contracts for ``_FallbackHttpHandler.do_POST``:

1. ``initialize`` reads ``_session.session_id`` to emit the ``mcp-session-id``
   header. With the production ``FileBackedSession`` that access re-reads the
   session JSON file; a corrupt/missing file raised out of ``do_POST`` before
   any byte was written, killing the response entirely. A session-id failure
   must degrade (no session header), never destroy the response.

2. The exec SSE streaming gate matched the literal name ``"exec"`` only, so
   the server-advertised aliases (``mcp__ralph__exec``, ``ralph_exec``)
   silently bypassed output streaming. Aliased exec calls must stream too.
"""

from __future__ import annotations

import io
import json
from email.message import Message

from ralph.mcp.server._fallback_http_handler import _FallbackHttpHandler
from ralph.mcp.server._fallback_http_server import _FallbackHttpServer
from ralph.mcp.server._json_rpc_response import JsonRpcResponse
from ralph.mcp.server._runtime_constants import DEFAULT_MOUNT_PATH
from ralph.mcp.server._server_state import ServerState


class _ExplodingSessionIdSession:
    """Mimics FileBackedSession.session_id when the backing file is corrupt."""

    def __init__(self) -> None:
        self.tool_output_sink_entry: object = None

    @property
    def session_id(self) -> str:
        raise ValueError("session file corrupt")


class _StreamingProbeSession:
    def __init__(self) -> None:
        self.tool_output_sink_entry: object = None

    session_id = "sess-1"


class _Mcp:
    def __init__(self, session: object) -> None:
        self._session = session
        self.sink_active_during_dispatch: list[bool] = []

    def handle_request(self, request: object, state: object) -> tuple[JsonRpcResponse, object]:
        self.sink_active_during_dispatch.append(
            getattr(self._session, "tool_output_sink_entry", None) is not None
        )
        msg_id = getattr(request, "msg_id", None)
        return (
            JsonRpcResponse(jsonrpc="2.0", result={"content": []}, msg_id=msg_id),
            state,
        )


class _Server(_FallbackHttpServer):
    """In-memory subclass of the production ``_FallbackHttpServer``.

    PROMPT.md proof obligation B requires the production handler to narrow
    ``self.server`` via ``isinstance`` (no ``cast()``), so the test harness
    must subclass the real class to pass that check.
    """

    def __init__(self, mcp: _Mcp) -> None:
        self.mcp_server = mcp
        self.state: object = ServerState.RUNNING

    def server_bind(self) -> None:
        """No-op: the in-memory harness never binds a real socket."""

    def server_activate(self) -> None:
        """No-op: the in-memory harness never activates a real listener."""


def _post(server: _Server, body: dict[str, object]) -> str:
    payload = json.dumps(body).encode("utf-8")
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
    handler.server = server

    handler.do_POST()
    return wfile.getvalue().decode("utf-8")


def test_initialize_with_corrupt_session_file_still_writes_response() -> None:
    server = _Server(_Mcp(_ExplodingSessionIdSession()))

    raw = _post(
        server,
        {"jsonrpc": "2.0", "method": "initialize", "id": "init-1", "params": {}},
    )

    assert raw, "initialize response was destroyed by a session-id read failure"
    assert "init-1" in raw


def test_exec_alias_tools_call_streams_output() -> None:
    for alias in ("mcp__ralph__exec", "ralph_exec"):
        mcp = _Mcp(_StreamingProbeSession())
        raw = _post(
            _Server(mcp),
            {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "id": "exec-1",
                "params": {"name": alias, "arguments": {"command": "echo hi"}},
            },
        )
        assert mcp.sink_active_during_dispatch == [True], (
            f"alias {alias!r} bypassed the exec streaming path"
        )
        assert "exec-1" in raw
