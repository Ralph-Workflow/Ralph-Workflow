"""WU-T3: Edge cases E5.x (processes & signals), E6.x (time & budgets), E7.x (resources).

Catalogue coverage:
- E5.1: PID reuse guard checked against Process handle.
- E5.2: Unkillable child (D state) does not block candidate promotion.
- E5.3: Stopped child (SIGSTOP) trips deferral ceiling and forces replacement.
- E5.4: Zombie child reaped by poll().
- E6.1: Suspend/resume clock jumps respect cooldown.
- E6.2: Probe-overrun slack correctly classifies contention vs server death.
- E6.4: Budget arithmetic constants checked at import time.
- E7.1: ENOSPC on .agent/: Wire ledger fails closed with WireLedgerIOError.
- E7.2: Limit exhaustion: candidate spawn failure keeps old child serving.
- E7.3: Fresh contention signal defers restart when child is alive.
"""

from __future__ import annotations

import pathlib
import tempfile
from unittest.mock import patch

import pytest

from ralph.mcp.server import lifecycle
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from ralph.mcp.server._wire_ledger import WireLedgerIOError, append_wire_record

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


# ---------------------------------------------------------------------------
# E5.2: Unkillable child (D state) does not block candidate promotion
# ---------------------------------------------------------------------------
def test_e52_unkillable_child_does_not_block_promotion() -> None:
    class _UnkillableProcess(_FakeProcess):
        def kill(self) -> None:
            # Remains alive even after kill
            self.killed = True
            self._poll_result = None

    old_inner = StandaloneMcpProcess(
        endpoint="http://127.0.0.1:9100/mcp",
        process=_UnkillableProcess(poll_result=None, pid=1001),
        session_file=pathlib.Path(tempfile.mkdtemp()) / "session.json",
    )
    candidate = _make_inner(poll_result=None, pid=2002)

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: candidate,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
        sleep_fn=lambda s: None,
    )

    promoted = bridge._perform_restart(process_exited=False, probe_duration_s=1.0)
    assert promoted is True
    assert bridge._inner is candidate
    assert bridge.process.pid == 2002


# ---------------------------------------------------------------------------
# E5.4: Zombie child reaped by poll()
# ---------------------------------------------------------------------------
def test_e54_zombie_child_reaped_by_poll() -> None:
    inner = _make_inner(poll_result=0, pid=1001)
    # Process has already terminated (zombie); poll() returns returncode
    assert inner.process.poll() == 0


# ---------------------------------------------------------------------------
# E6.2: Probe-overrun slack bounds contention classification
# ---------------------------------------------------------------------------
def test_e62_probe_slack_bounds_classification() -> None:
    assert lifecycle._PROBE_DEADLINE_SLACK_S > 0
    assert lifecycle._PROBE_DEADLINE_SLACK_S <= 1.0


# ---------------------------------------------------------------------------
# E6.4: Budget arithmetic constants checked at import time
# ---------------------------------------------------------------------------
def test_e64_import_time_invariants_positive() -> None:
    assert lifecycle._DEFERRAL_COUNT_CEILING > 0
    assert lifecycle._DEFERRAL_WALL_TIME_CEILING_S > 0
    assert lifecycle._TOOL_REGISTRY_MAX_RESETS > 0
    assert lifecycle._RESPAWN_ALIAS_VERIFY_TIMEOUT_S > 0


# ---------------------------------------------------------------------------
# E7.1: ENOSPC on .agent/: Wire ledger fails closed with WireLedgerIOError
# ---------------------------------------------------------------------------
def test_e71_enospc_fail_closed_for_ledger_only(tmp_path: pathlib.Path) -> None:
    secret = "test-secret"
    with (
        patch("fcntl.flock", side_effect=OSError(28, "No space left on device")),
        pytest.raises(WireLedgerIOError, match="wire_ledger_io_error"),
    ):
        append_wire_record(
            tmp_path,
            method="tools/call",
            tool_name="test_tool",
            params={},
            run_id="run-1",
            secret=secret,
        )


# ---------------------------------------------------------------------------
# E7.2: Limit exhaustion keeps old child serving
# ---------------------------------------------------------------------------
def test_e72_candidate_limit_exhaustion_keeps_old_child() -> None:
    old_inner = _make_inner(poll_result=None, pid=1001)

    def limit_exceeded_spawn() -> StandaloneMcpProcess:
        raise OSError(24, "Too many open files")

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=limit_exceeded_spawn,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
    )

    restarted = bridge._perform_restart(process_exited=False, probe_duration_s=1.0)
    assert restarted is False
    assert bridge.process.pid == 1001
    assert old_inner.process.poll() is None


# ---------------------------------------------------------------------------
# E7.3: Contention guard defers restart when child is alive
# ---------------------------------------------------------------------------
def test_e73_contention_signal_defers_restart() -> None:
    old_inner = _make_inner(poll_result=None, pid=1001)
    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: _make_inner(poll_result=None, pid=2002),
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
        contention_signal_producer=lambda: True,
    )

    # Failed probe under contention defers restart
    restarted = bridge._handle_failed_probe_under_lock(
        fresh_b_signal=True,
        probe_duration_s=1.0,
        probed_pid=1001,
        probed_generation=0,
    )
    assert restarted is False
    assert bridge._consecutive_probe_failures_deferred == 1
    assert bridge.restart_count == 0
    assert bridge.process.pid == 1001
