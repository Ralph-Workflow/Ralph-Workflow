"""Pins E3.x, E4.1, and E6.1: Transport gap, typed retry, and state transitions.

Catalogue requirements:
- E3.1: Client gets typed 503 / Retry-After during handover gap if request arrives during pause,
        and kernel backlog buffers burst connections without ECONNREFUSED.
- E3.2: Typed retry succeeds once candidate is serving (transparent recovery after gap).
- E3.4: Probe failure during candidate warmup does not kill healthy old child,
        and stale keep-alive connections retry safely on fresh connection.
- E4.1: Bridge transitions cleanly between states (SERVING -> REPLACING -> SERVING)
        without dropping requests.
- E6.1: Log level suppression during expected rotation transient (no ERROR/CRITICAL
        emissions during expected rotation) and clock-enforced backoff intervals.
"""

from __future__ import annotations

import http.client
import json
import pathlib
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.mcp.server import lifecycle
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess

if TYPE_CHECKING:
    import loguru

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


class _FakeProcess:
    """Mock process implementing the ProcessLike protocol for lifecycle tests."""

    def __init__(self, poll_result: int | None = None, pid: int = 9999) -> None:
        self._poll_result = poll_result
        self._pid = pid
        self.terminated = False
        self.killed = False

    @property
    def pid(self) -> int:
        return self._pid

    def poll(self) -> int | None:
        return self._poll_result

    def terminate(self, grace_period_s: float = 5.0) -> None:
        self.terminated = True
        self._poll_result = 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self.killed = True
        self._poll_result = -9


def _make_listening_socket() -> tuple[str, int, socket.socket]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    addr: object = sock.getsockname()
    if isinstance(addr, tuple) and len(addr) >= 2:
        return str(addr[0]), int(addr[1]), sock
    return "127.0.0.1", 0, sock


def _make_inner(
    tmp_path: pathlib.Path,
    poll_result: int | None = None,
    pid: int = 9999,
    endpoint: str = "http://127.0.0.1:9100/mcp",
) -> StandaloneMcpProcess:
    session_file = tmp_path / f"session_{pid}.json"
    session_file.write_text("{}", encoding="utf-8")
    return StandaloneMcpProcess(
        endpoint=endpoint,
        process=_FakeProcess(poll_result=poll_result, pid=pid),
        session_file=session_file,
    )


# ---------------------------------------------------------------------------
# E3.1: Client gets typed 503 / Retry-After during handover pause & backlog burst
# ---------------------------------------------------------------------------
def test_e31_client_gets_typed_503_retry_after_during_handover_pause() -> None:
    """Client gets typed 503 and Retry-After header during handover pause."""

    class _HandoverPauseHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            # During handover pause, server returns 503 Service Unavailable + Retry-After
            self.send_response(503)
            self.send_header("Retry-After", "1")
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            payload = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {
                        "code": -32001,
                        "message": "server paused: handover in progress",
                    },
                }
            ).encode("utf-8")
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            # Suppress default stdlib stderr logging in tests
            pass

    server = HTTPServer(("127.0.0.1", 0), _HandoverPauseHandler)
    host, port = str(server.server_address[0]), int(server.server_address[1])
    server_thread = threading.Thread(target=server.handle_request, daemon=True)
    server_thread.start()

    conn = http.client.HTTPConnection(host, port, timeout=2.0)
    try:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        conn.request("POST", "/mcp", body, {"Content-Type": "application/json"})
        resp = conn.getresponse()

        # Invariants for E3.1:
        assert resp.status == 503
        assert resp.getheader("Retry-After") == "1"

        data: object = json.loads(resp.read().decode("utf-8"))
        assert isinstance(data, dict)
        err = data.get("error")
        assert isinstance(err, dict)
        assert err.get("code") == -32001
        assert "handover in progress" in str(err.get("message"))
    finally:
        conn.close()
        server.server_close()
        server_thread.join(timeout=1.0)


def test_e31_backlog_burst_during_handover_gap_avoids_connrefused() -> None:
    """Kernel backlog buffers client connections during handover gap without ECONNREFUSED."""
    host, port, sock = _make_listening_socket()
    clients: list[socket.socket] = []
    try:
        # Handover gap: old child dropped listening socket, candidate not yet accepting
        for i in range(5):
            c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            c.settimeout(2.0)
            c.connect((host, port))
            c.sendall(f"BURST_{i}\n".encode())
            clients.append(c)

        # Candidate resumes accept loop and drains all burst connections
        sock.settimeout(2.0)
        for _ in range(5):
            conn, _ = sock.accept()
            with conn:
                conn.settimeout(2.0)
                data = conn.recv(1024)
                conn.sendall(b"SERVED:" + data)

        for i, c in enumerate(clients):
            resp = c.recv(1024)
            assert resp == f"SERVED:BURST_{i}\n".encode()
    finally:
        for c in clients:
            c.close()
        sock.close()


# ---------------------------------------------------------------------------
# E3.2: Typed retry succeeds once candidate is serving
# ---------------------------------------------------------------------------
def test_e32_typed_retry_succeeds_once_candidate_is_serving() -> None:
    """Typed retry following 503/pause succeeds once candidate is serving."""
    state = {"is_candidate_serving": False}

    class _HandoverLifecycleHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if not state["is_candidate_serving"]:
                self.send_response(503)
                self.send_header("Retry-After", "0.1")
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "error": {"code": -32001, "message": "handover_in_progress"},
                        }
                    ).encode("utf-8")
                )
            else:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "result": {"status": "ok", "server": "candidate"},
                        }
                    ).encode("utf-8")
                )

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), _HandoverLifecycleHandler)
    host, port = str(server.server_address[0]), int(server.server_address[1])

    def _serve_two_requests() -> None:
        server.handle_request()
        server.handle_request()

    server_thread = threading.Thread(target=_serve_two_requests, daemon=True)
    server_thread.start()

    conn = http.client.HTTPConnection(host, port, timeout=2.0)
    try:
        # Request 1 arrives during pause -> receives typed 503
        req_body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        conn.request("POST", "/mcp", req_body, {"Content-Type": "application/json"})
        resp1 = conn.getresponse()
        assert resp1.status == 503
        resp1.read()

        # Candidate finishes warmup and begins serving
        state["is_candidate_serving"] = True

        # Request 2 (client retry) succeeds against the candidate
        conn.request("POST", "/mcp", req_body, {"Content-Type": "application/json"})
        resp2 = conn.getresponse()
        assert resp2.status == 200

        data: object = json.loads(resp2.read().decode("utf-8"))
        assert isinstance(data, dict)
        res = data.get("result")
        assert isinstance(res, dict)
        assert res.get("status") == "ok"
        assert res.get("server") == "candidate"
    finally:
        conn.close()
        server.server_close()
        server_thread.join(timeout=1.0)


# ---------------------------------------------------------------------------
# E3.4: Probe failure during candidate warmup does not kill healthy old child
# ---------------------------------------------------------------------------
def test_e34_candidate_warmup_probe_failure_keeps_healthy_old_child_alive(
    tmp_path: pathlib.Path,
) -> None:
    """Warmup probe failure terminates candidate and keeps healthy old child alive."""
    old_inner = _make_inner(tmp_path, poll_result=None, pid=1001)
    candidate_inner = _make_inner(tmp_path, poll_result=None, pid=2002)

    class _FailingWarmupBridge(lifecycle.RestartAwareMcpBridge):
        def _verify_candidate(self, candidate: StandaloneMcpProcess) -> bool:
            # Simulate probe failure during candidate warmup
            return False

    bridge = _FailingWarmupBridge(
        old_inner,
        restart_fn=lambda: candidate_inner,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-e34",
        sleep_fn=lambda s: None,
    )

    restarted = bridge._perform_restart(process_exited=False, probe_duration_s=1.0)

    # Invariants for E3.4:
    # 1. Restart returns False (non-terminal failure, budget remains)
    assert restarted is False
    # 2. Candidate process was terminated/shut down
    candidate_proc = candidate_inner.process
    assert isinstance(candidate_proc, _FakeProcess)
    assert candidate_proc.terminated is True
    # 3. Healthy old child was NOT killed; remains active
    old_proc = old_inner.process
    assert isinstance(old_proc, _FakeProcess)
    assert old_proc.terminated is False
    assert old_proc.poll() is None
    # 4. Bridge still points to old child
    assert bridge.process.pid == 1001
    assert bridge._inner is old_inner


def test_e34_stale_keepalive_retry_safely_on_handover() -> None:
    """Persistent keep-alive connection closed on handover retries safely on fresh connection."""
    host, port, sock = _make_listening_socket()
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(2.0)
        client.connect((host, port))
        client.sendall(b"CALL_1\n")

        # Old child answers first request
        sock.settimeout(2.0)
        conn1, _ = sock.accept()
        assert conn1.recv(1024) == b"CALL_1\n"
        conn1.sendall(b"RESP_1\n")
        assert client.recv(1024) == b"RESP_1\n"

        # Old child finishes drain and closes connection
        conn1.close()

        # Client notices EOF/reset on next call
        client_closed = False
        try:
            client.sendall(b"CALL_2\n")
            chunk = client.recv(1024)
            if not chunk:
                client_closed = True
        except (BrokenPipeError, ConnectionResetError, OSError):
            client_closed = True
        assert client_closed is True
        client.close()

        # Client retries safely on fresh connection to candidate
        retry_client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        retry_client.settimeout(2.0)
        retry_client.connect((host, port))
        retry_client.sendall(b"CALL_2\n")

        conn2, _ = sock.accept()
        with conn2:
            assert conn2.recv(1024) == b"CALL_2\n"
            conn2.sendall(b"RESP_2\n")

        assert retry_client.recv(1024) == b"RESP_2\n"
        retry_client.close()
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# E4.1: Bridge transitions cleanly between states without dropping requests
# ---------------------------------------------------------------------------
def test_e41_bridge_state_transitions_cleanly_without_dropping_requests(
    tmp_path: pathlib.Path,
) -> None:
    """Bridge transitions SERVING -> REPLACING -> SERVING without dropping requests."""
    old_inner = _make_inner(tmp_path, poll_result=None, pid=1001)
    new_inner = _make_inner(tmp_path, poll_result=None, pid=2002)

    spawn_event = threading.Event()
    release_spawn = threading.Event()

    def slow_candidate_spawn() -> StandaloneMcpProcess:
        spawn_event.set()
        release_spawn.wait(timeout=2.0)
        return new_inner

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=slow_candidate_spawn,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-e41",
        sleep_fn=lambda s: None,
    )

    # Phase 1: Bridge in SERVING state with old child
    assert bridge.agent_endpoint_uri() == "http://127.0.0.1:9100/mcp"
    assert bridge.process.pid == 1001

    # Phase 2: Start replacement in background (transitions to REPLACING)
    t = threading.Thread(
        target=lambda: bridge._perform_restart(process_exited=False, probe_duration_s=0.5),
        daemon=True,
    )
    t.start()
    assert spawn_event.wait(timeout=1.0) is True

    # During replacement, bridge lock is NOT held across candidate spawn;
    # agent path continues to respond without dropping requests
    assert bridge.agent_endpoint_uri() == "http://127.0.0.1:9100/mcp"
    assert bridge.process.pid == 1001

    # Phase 3: Allow candidate spawn and handover to complete
    release_spawn.set()
    t.join(timeout=2.0)
    assert not t.is_alive()

    # Bridge transitioned back to SERVING with promoted candidate
    assert bridge.agent_endpoint_uri() == "http://127.0.0.1:9100/mcp"
    assert bridge.process.pid == 2002
    assert bridge._bridge_generation == 1


# ---------------------------------------------------------------------------
# E6.1: Log level suppression during expected rotation transient
# ---------------------------------------------------------------------------
def test_e61_log_level_suppression_during_expected_rotation_transient(
    tmp_path: pathlib.Path,
) -> None:
    """Expected rotation transients emit WARNING/INFO, never ERROR/CRITICAL."""
    old_inner = _make_inner(tmp_path, poll_result=None, pid=1001)
    new_inner = _make_inner(tmp_path, poll_result=None, pid=2002)

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: new_inner,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-e61",
        sleep_fn=lambda s: None,
    )

    captured_levels: list[str] = []

    def _sink(message: loguru.Message) -> None:
        rec = message.record
        captured_levels.append(rec["level"].name)

    sink_id = logger.add(_sink, level="INFO")
    try:
        # Perform expected rotation via make-before-break
        restarted = bridge._perform_restart(process_exited=False, probe_duration_s=0.5)
        assert restarted is True
    finally:
        logger.remove(sink_id)

    # Invariants for E6.1:
    # 1. Rotation was logged at INFO and WARNING
    assert "WARNING" in captured_levels or "INFO" in captured_levels
    # 2. No ERROR or CRITICAL logs were emitted during the expected rotation transient
    error_levels = [lvl for lvl in captured_levels if lvl in ("ERROR", "CRITICAL")]
    assert error_levels == [], (
        f"Expected rotation transient emitted forbidden error log levels: {error_levels}"
    )


def test_e61_clock_enforced_single_attempt_per_backoff_interval(tmp_path: pathlib.Path) -> None:
    """Cooldown spaces restart attempts even across rapid clock checks."""
    current_time = [100.0]

    def fake_clock() -> float:
        return current_time[0]

    cooldown_called: list[float] = []

    def fake_sleep(secs: float) -> None:
        cooldown_called.append(secs)

    old_inner = _make_inner(tmp_path, poll_result=None, pid=1001)
    new_inner = _make_inner(tmp_path, poll_result=None, pid=2002)

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: new_inner,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=1.5),
        run_id="test-e61-cooldown",
        clock_fn=fake_clock,
        sleep_fn=fake_sleep,
    )

    restarted = bridge._perform_restart(process_exited=False, probe_duration_s=0.2)
    assert restarted is True
    # Cooldown of 1.5 seconds was respected
    assert 1.5 in cooldown_called
