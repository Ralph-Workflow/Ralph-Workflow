"""WU-U3a: Edge case E1.8 old session_id accepted across rotation.

Pins catalogue requirement:
- E1.8: Session id identical across swap; unknown or old session_id is not rejected.
  The parent persists the session_id to snapshot before the first child serves.
  Subsequent candidate children inherit and serve with the same session_id,
  and incoming requests carrying pre-rotation or unfamiliar session IDs are
  safely accepted rather than rejected.
"""

from __future__ import annotations

import contextlib
import http.client
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.protocol.env import (
    MCP_SNAPSHOT_DIR_ENV,
)
from ralph.mcp.server._handover_protocol import (
    create_handover_socketpair,
    start_private_readiness_listener,
    verify_candidate_readiness,
)
from ralph.mcp.server._json_rpc_response import JsonRpcResponse
from ralph.mcp.server._snapshots import (
    snapshot_dir,
    write_snapshot,
)
from ralph.mcp.server.lifecycle import (
    MCP_SESSION_ID_ENV,
)
from ralph.mcp.server.runtime import FallbackStandaloneServer as _FallbackStandaloneServer
from ralph.mcp.server.runtime import ServerState
from ralph.mcp.server.runtime_session import (
    FileBackedSession,
)
from ralph.process.manager import SpawnOptions, get_process_manager

if TYPE_CHECKING:
    from ralph.process.manager import ManagedProcess

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


def _reap_process(proc: ManagedProcess) -> None:
    if proc.poll() is None:
        with contextlib.suppress(Exception):
            proc.kill()
            proc.wait(timeout=2.0)


class _StubMcpServer:
    def __init__(self, session_id: str) -> None:
        class _StubSession:
            def __init__(self, sid: str) -> None:
                self.session_id = sid
                self.broker_secret = None

        self._session = _StubSession(session_id)
        self._registry = SimpleRegistry()

    def handle_request(
        self,
        request: object,
        state: ServerState,
    ) -> tuple[JsonRpcResponse, ServerState]:
        msg_id: object = getattr(request, "msg_id", 1)
        return (
            JsonRpcResponse(jsonrpc="2.0", msg_id=msg_id, result={"accepted": True}),
            ServerState.RUNNING,
        )


class SimpleRegistry:
    def __init__(self) -> None:
        self.tools: list[dict[str, object]] = []


# ---------------------------------------------------------------------------
# E1.8: Session ID persisted before first child serves and reused across candidate
# ---------------------------------------------------------------------------
def test_session_id_persisted_before_first_child_serves_and_inherited(tmp_path: Path) -> None:
    """Parent persists session_id to snapshot before first child serves; candidate reuses it."""
    run_id = "run-e18-persistence"
    original_session_id = "session-e18-stable-42"
    snap_dir = snapshot_dir(tmp_path, run_id)

    # 1. Parent writes snapshot file before first child serves
    write_snapshot(
        snap_dir,
        session_id=original_session_id,
        run_id=run_id,
        broker_secret_fingerprint=None,
    )
    assert (snap_dir / "session.json").exists()

    # 2. Candidate child environment is configured to read the snapshot
    candidate_session_file = tmp_path / "candidate_session.json"
    # An empty or run_id-only session file causes runtime_session.py to resolve via snapshot
    candidate_session_file.write_text(
        json.dumps({"run_id": run_id}),
        encoding="utf-8",
    )

    file_backed = FileBackedSession(
        candidate_session_file,
        workspace_root=tmp_path,
    )
    # Invariant: Candidate resolves the exact session_id established by parent
    assert file_backed.session_id == original_session_id

    # 3. Private candidate preflight over socketpair checks expected_session_id
    parent_sock, child_sock = create_handover_socketpair()
    try:
        mock_server = _StubMcpServer(original_session_id)
        start_private_readiness_listener(
            child_sock,
            mock_server,
            stage_id="stage-cand-e18",
            session_id=file_backed.session_id,
            broker_fingerprint=None,
        )

        ok, stage, err = verify_candidate_readiness(
            parent_sock,
            expected_spawn_id="stage-cand-e18",
            expected_pid=os.getpid(),
            expected_session_id=original_session_id,
            expected_broker_fingerprint=None,
            timeout=2.0,
        )
        assert ok is True, f"Candidate readiness check failed: stage={stage}, err={err}"
    finally:
        parent_sock.close()
        child_sock.close()


# ---------------------------------------------------------------------------
# E1.8: Standalone HTTP server accepts request carrying old or unknown session_id
# ---------------------------------------------------------------------------
def test_standalone_server_accepts_old_session_id_header(tmp_path: Path) -> None:
    """Incoming JSON-RPC requests carrying old pre-rotation session IDs are accepted."""
    current_session_id = "session-current-active"
    old_pre_rotation_session_id = "session-old-from-previous-rotation"

    server = _FallbackStandaloneServer("127.0.0.1", 0, _StubMcpServer(current_session_id))
    httpd = server.prepare_httpd()
    ready = threading.Event()
    stop_event = threading.Event()

    server_thread = threading.Thread(
        target=server.run,
        kwargs={"ready_event": ready, "stop_event": stop_event},
        daemon=True,
    )
    server_thread.start()
    assert ready.wait(timeout=5.0), "Server failed to start"

    host, port = server.bound_address
    conn = http.client.HTTPConnection(host, port, timeout=5.0)

    try:
        # Request with an old session_id header from a previous rotation
        headers = {
            "Content-Type": "application/json",
            "mcp-session-id": old_pre_rotation_session_id,
        }
        body = json.dumps({"jsonrpc": "2.0", "id": 101, "method": "tools/list", "params": {}})
        conn.request("POST", "/mcp", body=body, headers=headers)
        resp = conn.getresponse()

        # Invariant: Server does NOT reject the old session ID
        assert resp.status == 200, f"Server rejected old session ID with status {resp.status}"
        resp_headers = dict(resp.getheaders())
        assert resp_headers.get("mcp-session-id") is None or isinstance(
            resp_headers.get("mcp-session-id"), str
        )
        body_bytes = resp.read()
        assert b'"jsonrpc": "2.0"' in body_bytes
        assert b'"accepted": true' in body_bytes
    finally:
        conn.close()
        stop_event.set()
        httpd.server_close()


def test_standalone_server_accepts_completely_unknown_session_id() -> None:
    """Incoming requests carrying completely unknown or arbitrary session IDs succeed."""
    server = _FallbackStandaloneServer("127.0.0.1", 0, _StubMcpServer("session-default"))
    httpd = server.prepare_httpd()
    ready = threading.Event()
    stop_event = threading.Event()

    server_thread = threading.Thread(
        target=server.run,
        kwargs={"ready_event": ready, "stop_event": stop_event},
        daemon=True,
    )
    server_thread.start()
    assert ready.wait(timeout=5.0)

    host, port = server.bound_address
    conn = http.client.HTTPConnection(host, port, timeout=5.0)

    try:
        headers = {
            "Content-Type": "application/json",
            "mcp-session-id": "unknown-session-uuid-99999",
        }
        body = json.dumps({"jsonrpc": "2.0", "id": 202, "method": "initialize", "params": {}})
        conn.request("POST", "/mcp", body=body, headers=headers)
        resp = conn.getresponse()

        # Invariant: Request with unknown session ID is accepted
        assert resp.status == 200
        body_bytes = resp.read()
        assert b'"jsonrpc": "2.0"' in body_bytes
    finally:
        conn.close()
        stop_event.set()
        httpd.server_close()


# ---------------------------------------------------------------------------
# E1.8: Real child subprocess accepts old session ID across simulated rotation
# ---------------------------------------------------------------------------
def test_real_child_accepts_old_session_id_across_rotation(tmp_path: Path) -> None:
    """Real child candidate spawned in rotation accepts requests carrying old session ID."""
    ralph_root = str(Path(__file__).resolve().parents[2])
    port_file = tmp_path / "port_e18.txt"
    run_id = "run-e18-rot"
    stable_session_id = "session-e18-carried"

    # Pre-write snapshot as parent would do on first spawn
    snap_dir = snapshot_dir(tmp_path, run_id)
    write_snapshot(
        snap_dir,
        session_id=stable_session_id,
        run_id=run_id,
        broker_secret_fingerprint=None,
    )

    child_script = tmp_path / "candidate_child.py"
    child_script.write_text(
        "\n".join(
            [
                "import sys, time, threading",
                "from pathlib import Path",
                f"sys.path.insert(0, {ralph_root!r})",
                "from ralph.mcp.server.runtime import FallbackStandaloneServer as _FallbackStandaloneServer",
                "from ralph.mcp.server._json_rpc_response import JsonRpcResponse",
                "from ralph.mcp.server.runtime import ServerState",
                "",
                "class _CandidateSession:",
                f"    session_id = {stable_session_id!r}",
                "",
                "class _CandidateServer:",
                "    _session = _CandidateSession()",
                "    def handle_request(self, req, state):",
                "        return JsonRpcResponse(jsonrpc='2.0', msg_id=req.msg_id, result={'candidate_ok': True}), ServerState.RUNNING",
                "",
                "srv = _FallbackStandaloneServer('127.0.0.1', 0, _CandidateServer())",
                "httpd = srv.prepare_httpd()",
                f"Path({str(port_file)!r}).write_text(str(srv.bound_address[1]), encoding='utf-8')",
                "ready = threading.Event()",
                "srv.run(ready_event=ready)",
            ]
        ),
        encoding="utf-8",
    )

    env = dict(os.environ)
    env[MCP_SESSION_ID_ENV] = stable_session_id
    env[MCP_SNAPSHOT_DIR_ENV] = str(snap_dir)

    pm = get_process_manager()
    proc = pm.spawn(
        [sys.executable, str(child_script)],
        SpawnOptions(cwd=str(tmp_path), env=env, label="e18-candidate-child"),
    )

    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not port_file.exists():
            time.sleep(0.02)
        assert port_file.exists()
        port = int(port_file.read_text(encoding="utf-8").strip())

        # Old client from prior session connects with old session ID
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5.0)
        try:
            headers = {
                "Content-Type": "application/json",
                "mcp-session-id": "old-rotated-session-prior",
            }
            body = json.dumps({"jsonrpc": "2.0", "id": 303, "method": "tools/call", "params": {}})
            conn.request("POST", "/mcp", body=body, headers=headers)
            resp = conn.getresponse()
            assert resp.status == 200
            data = resp.read()
            assert b'"candidate_ok": true' in data
        finally:
            conn.close()
    finally:
        _reap_process(proc)

    # Leak check: child is terminated
    assert proc.poll() is not None
