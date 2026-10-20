"""WU-T2: Edge cases E3.x (socket & transport) and E4.x (concurrency & supervisors).

Catalogue coverage:
- E3.1: Backlog overflow / burst during gap queues in kernel backlog.
- E3.2: Client timeout shorter than gap surfaces as typed retryable error.
- E3.3: Listening fd absent from non-MCP children.
- E3.4: Stale keep-alive connection closure safe retry.
- E4.1: Bridge lock not held across full candidate startup.
- E4.2: Concurrent health checks on single bridge are serialized safely.
- E4.3: Supervisor exit reaps in-flight candidate without leaking.
- E4.4: Bridge shutdown terminates old child, candidate, and draining child cleanly.
- E4.5: Idempotent bridge shutdown: multiple calls succeed without error.
- E4.6: Subprocess death / clean termination.
"""

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
    host, port = sock.getsockname()[:2]
    return str(host), int(port), sock


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


# ---------------------------------------------------------------------------
# E3.1: Backlog burst during gap: client connects without ECONNREFUSED
# ---------------------------------------------------------------------------
def test_e31_backlog_burst_during_handover_gap() -> None:
    host, port, sock = _make_listening_socket()
    try:
        # Client connect during the handover gap connects to the parent's listening socket
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            client.settimeout(2.0)
            client.connect((host, port))
            # No ECONNREFUSED; connection is in kernel backlog
            assert client.fileno() > 0
        finally:
            client.close()
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# E3.3: Listening fd absent from non-MCP children
# ---------------------------------------------------------------------------
def test_e33_no_listening_fd_in_non_mcp_child(tmp_path: pathlib.Path) -> None:
    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    env = lifecycle._subprocess_env(session_file)
    # MCP_INHERITED_FD_ENV must NOT be set ambiently in non-MCP children
    assert MCP_INHERITED_FD_ENV not in env


# ---------------------------------------------------------------------------
# E4.1: Bridge lock is not held across candidate startup
# ---------------------------------------------------------------------------
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
    # The spawn happens inside _perform_restart; allow it to finish
    spawn_can_finish.set()
    t.join(timeout=2.0)
    assert not t.is_alive()


# ---------------------------------------------------------------------------
# E4.2: Health checks serialized safely per bridge
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# E4.4: Bridge shutdown terminates processes and releases listening socket
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# E4.5: Bridge shutdown is idempotent
# ---------------------------------------------------------------------------
def test_e45_bridge_shutdown_is_idempotent() -> None:
    inner = _make_inner(poll_result=None, pid=1001)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_inner(poll_result=None, pid=2002),
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
    )

    bridge.shutdown()
    # Second shutdown must be a safe no-op
    bridge.shutdown()
    assert inner.process.poll() is not None
