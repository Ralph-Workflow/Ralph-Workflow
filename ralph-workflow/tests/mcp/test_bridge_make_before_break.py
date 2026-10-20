"""WU-Bridge: make-before-break MCP restart state machine tests.

Pins C1 make-before-break behavior:
- Live old child continues serving while candidate is spawned.
- Unverified candidate (exited/crashed) is discarded without promoting.
- Successful candidate is promoted to _inner while old child is drained.
- Failed restart attempt keeps old child serving and does not break the bridge.
- Budget exhaustion occurs only after max_restarts failed attempts.
"""

from __future__ import annotations

import pathlib
import tempfile
import time

import pytest

from ralph.mcp.server import lifecycle
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess

pytestmark = [pytest.mark.subprocess_e2e]


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


def test_live_server_survives_failed_candidate() -> None:
    """A live old child keeps serving when candidate spawn fails."""
    inner = _make_inner(poll_result=None, pid=1001)

    def failing_restart_fn() -> StandaloneMcpProcess:
        raise RuntimeError("candidate spawn failed")

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=failing_restart_fn,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
    )

    result = bridge._perform_restart(process_exited=False, probe_duration_s=1.0)
    assert result is False
    assert bridge.process.pid == 1001
    assert bridge.restart_count == 1
    assert inner.process.poll() is None


def test_make_before_break_promotion_with_old_drain() -> None:
    """Successful candidate is promoted and old child is drained in background."""
    old_inner = _make_inner(poll_result=None, pid=1001)
    candidate = _make_inner(poll_result=None, pid=2002)

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: candidate,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
        sleep_fn=lambda s: None,
    )

    result = bridge._perform_restart(process_exited=False, probe_duration_s=1.0)
    assert result is True
    assert bridge.process.pid == 2002
    assert bridge._inner is candidate

    # Give background drain thread a moment to finish
    time.sleep(0.05)
    assert old_inner.process.poll() is not None


def test_preflight_over_shared_endpoint_does_not_promote_unverified_candidate() -> None:
    """Candidate that exited before promotion is not promoted; old child is kept."""
    old_inner = _make_inner(poll_result=None, pid=1001)
    dead_candidate = _make_inner(poll_result=1, pid=3003)

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: dead_candidate,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
    )

    result = bridge._perform_restart(process_exited=False, probe_duration_s=1.0)
    assert result is False
    assert bridge.process.pid == 1001
    assert bridge._inner is old_inner


def test_single_failed_attempt_does_not_set_inner_to_drained_inner() -> None:
    """A single failed attempt does not set _inner to a shut-down inner."""
    old_inner = _make_inner(poll_result=None, pid=1001)

    def failing_restart_fn() -> StandaloneMcpProcess:
        raise OSError("fork failed")

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=failing_restart_fn,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
    )

    result = bridge._perform_restart(process_exited=False, probe_duration_s=1.0)
    assert result is False
    assert bridge.process.pid == 1001
    assert bridge._inner is old_inner
    assert old_inner.process.poll() is None


def test_budget_still_exhausts_after_n_failed_attempts_only() -> None:
    """Repeated failed attempts exhaust budget only after max_restarts reached."""
    old_inner = _make_inner(poll_result=None, pid=1001)

    def failing_restart_fn() -> StandaloneMcpProcess:
        raise RuntimeError("synthetic candidate failure")

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=failing_restart_fn,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
    )

    for i in range(1, 4):
        assert bridge._perform_restart(process_exited=False, probe_duration_s=1.0) is False
        assert bridge.restart_count == i
        assert bridge._inner is old_inner

    with pytest.raises(McpServerError, match="restart budget exhausted"):
        bridge._perform_restart(process_exited=False, probe_duration_s=1.0)
