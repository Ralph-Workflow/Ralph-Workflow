from __future__ import annotations

import pathlib
import socket
import tempfile
import threading

import pytest

from ralph.mcp.protocol.env import MCP_INHERITED_FD_ENV
from ralph.mcp.server import lifecycle
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess

pytestmark = [pytest.mark.subprocess_e2e]


def _make_listening_socket() -> tuple[str, int, socket.socket]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    addr: object = sock.getsockname()
    if isinstance(addr, tuple) and len(addr) >= 2:
        return str(addr[0]), int(addr[1]), sock
    return "127.0.0.1", 0, sock


class _FakeProcess:
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


def _make_inner(
    poll_result: int | None = None,
    pid: int = 9999,
    endpoint: str = "http://127.0.0.1:9100/mcp",
) -> StandaloneMcpProcess:
    session_file = pathlib.Path(tempfile.mkdtemp()) / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    return StandaloneMcpProcess(
        endpoint=endpoint,
        process=_FakeProcess(poll_result=poll_result, pid=pid),
        session_file=session_file,
    )


def test_e31_backlog_burst_during_handover_gap_accepted_and_answered() -> None:
    host, port, sock = _make_listening_socket()
    clients: list[socket.socket] = []
    try:
        for i in range(3):
            c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            c.settimeout(2.0)
            c.connect((host, port))
            c.sendall(f"REQ_{i}\n".encode())
            clients.append(c)

        sock.settimeout(2.0)
        for _ in range(3):
            conn, _ = sock.accept()
            with conn:
                conn.settimeout(2.0)
                data = conn.recv(1024)
                conn.sendall(b"ACK:" + data)

        for i, c in enumerate(clients):
            resp = c.recv(1024)
            assert resp == f"ACK:REQ_{i}\n".encode()
    finally:
        for c in clients:
            c.close()
        sock.close()


def test_e32_client_timeout_shorter_than_gap_surfaces_retryable_error() -> None:
    host, port, sock = _make_listening_socket()
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(0.05)
        client.connect((host, port))
        client.sendall(b"PING\n")
        with pytest.raises((TimeoutError, socket.timeout)):
            client.recv(1024)
        client.close()

        sock.settimeout(1.0)
        stale_conn, _ = sock.accept()
        stale_conn.close()

        retry_client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        retry_client.settimeout(1.0)
        retry_client.connect((host, port))
        retry_client.sendall(b"RETRY_PING\n")

        conn, _ = sock.accept()
        with conn:
            conn.settimeout(1.0)
            data = conn.recv(1024)
            conn.sendall(b"PONG:" + data)

        resp = retry_client.recv(1024)
        assert resp == b"PONG:RETRY_PING\n"
        retry_client.close()
    finally:
        sock.close()


def test_e33_no_listening_fd_in_non_mcp_child(tmp_path: pathlib.Path) -> None:
    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    env = lifecycle._subprocess_env(session_file)
    assert MCP_INHERITED_FD_ENV not in env


def test_e34_persistent_connection_closed_on_handover_retries_safely() -> None:
    host, port, sock = _make_listening_socket()
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(2.0)
        client.connect((host, port))
        client.sendall(b"REQ1\n")

        sock.settimeout(2.0)
        conn1, _ = sock.accept()
        data1 = conn1.recv(1024)
        assert data1 == b"REQ1\n"
        conn1.sendall(b"RESP1\n")
        assert client.recv(1024) == b"RESP1\n"

        conn1.close()

        closed = False
        try:
            client.sendall(b"REQ2\n")
            read_chunk = client.recv(1024)
            if not read_chunk:
                closed = True
        except (BrokenPipeError, ConnectionResetError, OSError):
            closed = True
        assert closed is True
        client.close()

        retry_client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        retry_client.settimeout(2.0)
        retry_client.connect((host, port))
        retry_client.sendall(b"REQ2\n")

        conn2, _ = sock.accept()
        with conn2:
            data2 = conn2.recv(1024)
            assert data2 == b"REQ2\n"
            conn2.sendall(b"RESP2\n")

        assert retry_client.recv(1024) == b"RESP2\n"
        retry_client.close()
    finally:
        sock.close()


def test_e41_lock_released_during_candidate_spawn() -> None:
    old_inner = _make_inner(poll_result=None, pid=1001)
    spawn_started = threading.Event()
    spawn_can_finish = threading.Event()

    def slow_candidate_spawn() -> StandaloneMcpProcess:
        spawn_started.set()
        spawn_can_finish.wait(timeout=2.0)
        return _make_inner(poll_result=None, pid=2002)

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=slow_candidate_spawn,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
        sleep_fn=lambda s: None,
    )

    t = threading.Thread(
        target=lambda: bridge._perform_restart(process_exited=False, probe_duration_s=1.0),
        daemon=True,
    )
    t.start()
    spawn_started.wait(timeout=1.0)
    assert bridge.agent_endpoint_uri() == "http://127.0.0.1:9100/mcp"
    assert bridge._inner.process.pid == 1001

    spawn_can_finish.set()
    t.join(timeout=2.0)
    assert not t.is_alive()
    assert bridge._inner.process.pid == 2002


def test_e42_concurrent_health_checks_serialized() -> None:
    inner = _make_inner(poll_result=None, pid=1001)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_inner(poll_result=None, pid=2002),
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
    )

    results: list[bool] = []

    def check() -> None:
        results.append(bridge.check_health_and_restart_if_needed())

    t1 = threading.Thread(target=check)
    t2 = threading.Thread(target=check)
    t1.start()
    t2.start()
    t1.join(timeout=1.0)
    t2.join(timeout=1.0)

    assert len(results) == 2
    assert results == [False, False]


def test_e43_supervisor_exit_cancels_inflight_replacement_without_leak() -> None:
    _host, _port, sock = _make_listening_socket()
    old_inner = _make_inner(poll_result=None, pid=1001)
    candidate_proc = _FakeProcess(poll_result=None, pid=2002)
    candidate_session = pathlib.Path(tempfile.mkdtemp()) / "session.json"
    candidate_session.write_text("{}", encoding="utf-8")
    candidate = StandaloneMcpProcess(
        endpoint="http://127.0.0.1:9100/mcp",
        process=candidate_proc,
        session_file=candidate_session,
    )

    class _FailingVerifyBridge(lifecycle.RestartAwareMcpBridge):
        def _verify_candidate(self, candidate_inner: StandaloneMcpProcess) -> bool:
            return False

    bridge = _FailingVerifyBridge(
        old_inner,
        restart_fn=lambda: candidate,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
    )
    listening_socket_holder: list[socket.socket | None] = [sock]
    bridge._listening_socket = listening_socket_holder[0]

    restarted = bridge._perform_restart(process_exited=False, probe_duration_s=1.0)
    assert restarted is False
    assert candidate_proc.terminated is True

    bridge.shutdown()
    assert old_inner.process.poll() is not None
    assert bridge._listening_socket is None
    with pytest.raises(OSError):
        sock.getsockname()


def test_e44_bridge_shutdown_cleans_resources() -> None:
    _host, _port, sock = _make_listening_socket()
    inner = _make_inner(poll_result=None, pid=1001)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_inner(poll_result=None, pid=2002),
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
    )
    bridge._listening_socket = sock

    bridge.shutdown()
    assert inner.process.poll() is not None
    assert bridge._listening_socket is None


def test_e45_bridge_shutdown_is_idempotent() -> None:
    inner = _make_inner(poll_result=None, pid=1001)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_inner(poll_result=None, pid=2002),
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
    )

    bridge.shutdown()
    bridge.shutdown()
    assert inner.process.poll() is not None


def test_e46_candidate_death_on_probation_triggers_immediate_replacement() -> None:
    old_inner = _make_inner(poll_result=None, pid=1001)
    candidate = _make_inner(poll_result=None, pid=2002)
    replacement = _make_inner(poll_result=None, pid=3003)

    spawn_counts = {"candidate": 0, "replacement": 0}

    def restart_fn() -> StandaloneMcpProcess:
        if spawn_counts["candidate"] == 0:
            spawn_counts["candidate"] += 1
            return candidate
        spawn_counts["replacement"] += 1
        return replacement

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=restart_fn,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
        sleep_fn=lambda s: None,
    )

    promoted = bridge._perform_restart(process_exited=False, probe_duration_s=1.0)
    assert promoted is True
    assert bridge.process.pid == 2002

    cast_proc = candidate.process
    assert isinstance(cast_proc, _FakeProcess)
    cast_proc._poll_result = 137

    restarted = bridge.check_health_and_restart_if_needed()
    assert restarted is True
    assert bridge.restart_count == 2
    assert bridge.process.pid == 3003
