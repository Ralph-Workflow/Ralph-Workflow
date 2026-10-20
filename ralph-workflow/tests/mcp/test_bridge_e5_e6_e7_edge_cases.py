"""WU-T3: Edge cases E5.x (processes & signals), E6.x (time & budgets), E7.x (resources).

Catalogue coverage:
- E5.1: PID reuse guard checked against Process handle and bridge generation.
- E5.2: Unkillable child (D state) does not block candidate promotion.
- E5.3: Stopped child (SIGSTOP) trips deferral ceiling and forces replacement.
- E5.4: Zombie child reaped by poll().
- E6.1: Suspend/resume clock jumps respect cooldown.
- E6.2: Probe-overrun slack correctly classifies contention vs server death.
- E6.3: Flapping (alternating success and failure) resets consecutive failure counter.
- E6.4: Budget arithmetic constants checked at import time.
- E7.1: ENOSPC on .agent/: Wire ledger fails closed with WireLedgerIOError.
- E7.2: Limit exhaustion: candidate spawn failure keeps old child serving.
- E7.3: Fresh contention signal defers restart when child is alive.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import time
from datetime import timedelta
from unittest.mock import patch

import psutil
import pytest

from ralph.mcp.server import lifecycle
from ralph.mcp.server._handover_protocol import (
    _HANDOVER_ACK_TIMEOUT_S,
    _HANDOVER_DRAIN_S,
)
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
# E5.1: PID reuse guard checked against Process handle and bridge generation
# ---------------------------------------------------------------------------
def test_e51_pid_reuse_guard_anchored_to_bridge_generation() -> None:
    inner = _make_inner(poll_result=None, pid=1001)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_inner(poll_result=None, pid=1001),  # OS reuses same PID 1001
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
    )
    initial_gen = bridge._bridge_generation

    # Generation increments on restart / tool-registry-reset
    bridge._bridge_generation += 1

    # Stale probe from prior generation with same PID is discarded
    restarted = bridge._handle_failed_probe_under_lock(
        fresh_b_signal=False,
        probe_duration_s=1.0,
        probed_pid=1001,
        probed_generation=initial_gen,
    )
    assert restarted is False
    assert bridge.restart_count == 0


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
# E5.3: Stopped child (SIGSTOP) trips deferral ceiling and forces replacement
# ---------------------------------------------------------------------------
def test_e53_stopped_child_trips_deferral_ceiling_and_forces_replacement() -> None:
    # Stopped child is alive in kernel (poll() is None) but unresponsive
    stopped_inner = _make_inner(poll_result=None, pid=1001)
    replacement = _make_inner(poll_result=None, pid=2002)

    bridge = lifecycle.RestartAwareMcpBridge(
        stopped_inner,
        restart_fn=lambda: replacement,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
        sleep_fn=lambda s: None,
    )

    # Accumulate deferrals up to the count ceiling
    for _ in range(lifecycle._DEFERRAL_COUNT_CEILING):
        restarted = bridge._handle_failed_probe_under_lock(
            fresh_b_signal=True,
            probe_duration_s=1.0,
            probed_pid=1001,
            probed_generation=0,
        )
        assert restarted is False

    assert bridge._consecutive_probe_failures_deferred == lifecycle._DEFERRAL_COUNT_CEILING

    # Next failure trips the ceiling and forces replacement despite fresh contention signal
    restarted = bridge._handle_failed_probe_under_lock(
        fresh_b_signal=True,
        probe_duration_s=1.0,
        probed_pid=1001,
        probed_generation=0,
    )
    assert restarted is True
    assert bridge.process.pid == 2002
    assert bridge.restart_count == 1


# ---------------------------------------------------------------------------
# E5.4: Zombie child reaped by poll()
# ---------------------------------------------------------------------------
def test_e54_zombie_child_reaped_by_poll() -> None:
    """E5.4: OS zombie child is reaped by poll(), proving os.kill(pid, 0) is not a liveness test.

    A terminated child process remains in the OS process table as a zombie until
    the parent reaps it (via waitpid/poll). During this zombie state:
      1. os.kill(pid, 0) succeeds, demonstrating that signal delivery check
         alone cannot distinguish a living process from a dead unreaped zombie.
      2. proc.poll() issues waitpid(WNOHANG), reaping the zombie and returning its exit code.
      3. After poll() reaps the zombie, os.kill(pid, 0) raises ProcessLookupError (ESRCH),
         confirming the process has been completely removed from the OS table.
    """
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(0)"])
    try:
        p = psutil.Process(proc.pid)
        deadline = time.monotonic() + 2.0
        observed_zombie = False
        while time.monotonic() < deadline:
            try:
                if p.status() == psutil.STATUS_ZOMBIE:
                    observed_zombie = True
                    break
            except psutil.NoSuchProcess:
                break
            time.sleep(0.01)

        # 0. The zombie state was ACTUALLY observed. Without this the two
        #    assertions below would also hold for a still-running child,
        #    because os.kill(pid, 0) succeeds on a live process too — the
        #    test would stop proving that a zombie is liveness-indistinguishable.
        assert observed_zombie, "child must be observed in the OS zombie state before reaping"

        # 1. os.kill(pid, 0) succeeds on the zombie child -> NOT a liveness test
        os.kill(proc.pid, 0)

        # 2. poll() reaps the zombie child and returns its returncode
        returncode = proc.poll()
        assert returncode == 0

        # 3. After poll() has reaped it, the PID is gone from the OS table
        with pytest.raises(ProcessLookupError):
            os.kill(proc.pid, 0)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=1.0)


# ---------------------------------------------------------------------------
# E6.1: Suspend/resume clock jumps respect cooldown and trip wall-time ceiling
# ---------------------------------------------------------------------------
def test_e61_suspend_resume_clock_jump_trips_wall_time_ceiling() -> None:
    current_time = [1000.0]

    def fake_clock() -> float:
        return current_time[0]

    inner = _make_inner(poll_result=None, pid=1001)
    replacement = _make_inner(poll_result=None, pid=2002)

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: replacement,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
        clock_fn=fake_clock,
        sleep_fn=lambda s: None,
    )

    # First deferral records start time
    restarted = bridge._handle_failed_probe_under_lock(
        fresh_b_signal=True,
        probe_duration_s=1.0,
        probed_pid=1001,
        probed_generation=0,
    )
    assert restarted is False
    assert bridge._consecutive_probe_failures_deferred == 1

    # Host suspends and resumes: clock jumps past wall-time ceiling
    current_time[0] += lifecycle._DEFERRAL_WALL_TIME_CEILING_S + 10.0

    # Next failure trips wall-time ceiling and forces replacement
    restarted = bridge._handle_failed_probe_under_lock(
        fresh_b_signal=True,
        probe_duration_s=1.0,
        probed_pid=1001,
        probed_generation=0,
    )
    assert restarted is True
    assert bridge.process.pid == 2002


# ---------------------------------------------------------------------------
# E6.2: Probe-overrun slack bounds contention classification
# ---------------------------------------------------------------------------
def test_e62_probe_slack_bounds_classification() -> None:
    """Constant guard for E6.2.

    The BEHAVIOURAL proof of E6.2 -- that an overrun past ``timeout + slack`` is
    classified as contention rather than a server failure, and that the
    deferral ceiling still forces a restart for a genuinely hung server -- lives
    in
    ``tests/mcp/test_probe_overrun_self_measure.py::test_e62_probe_overrun_is_contention_and_deferral_ceiling_is_the_backstop``.
    These assertions only pin the constant the behavioural test exercises.
    """
    assert lifecycle._PROBE_DEADLINE_SLACK_S > 0
    assert lifecycle._PROBE_DEADLINE_SLACK_S <= 1.0


# ---------------------------------------------------------------------------
# E6.3: Flapping (alternating success and failure) hysteresis retains failure counter
# ---------------------------------------------------------------------------
def test_e63_flapping_server_resets_consecutive_failures() -> None:
    inner = _make_inner(poll_result=None, pid=1001)
    # Sequence: Fail, Pass, Fail, Pass, Pass, Pass, Fail
    probe_results = [True, False, True, False, False, False, True]  # True = fail, False = pass

    def probe_fn(endpoint: str, timeout: timedelta) -> None:
        failed = probe_results.pop(0)
        if failed:
            raise RuntimeError("probe timed out")

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_inner(poll_result=None, pid=2002),
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
        probe_fn=probe_fn,
        sleep_fn=lambda s: None,
    )

    # 1st probe fails -> consecutive failures = 1, successes = 0
    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge._consecutive_probe_failures == 1
    assert bridge._consecutive_probe_successes == 0

    # 2nd probe passes (1 success) -> consecutive failures RETAINED at 1 (not cleared by lucky probe)
    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge._consecutive_probe_failures == 1
    assert bridge._consecutive_probe_successes == 1

    # 3rd probe fails -> consecutive failures incremented to 2, successes reset to 0
    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge._consecutive_probe_failures == 2
    assert bridge._consecutive_probe_successes == 0

    # 4th probe passes (1 success) -> failures still retained at 2
    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge._consecutive_probe_failures == 2
    assert bridge._consecutive_probe_successes == 1

    # 5th probe passes (2 consecutive successes) -> failures still retained at 2
    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge._consecutive_probe_failures == 2
    assert bridge._consecutive_probe_successes == 2

    # 6th probe passes (3 consecutive successes == _PROBE_SUCCESS_HYSTERESIS_COUNT) -> reset to 0!
    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge._consecutive_probe_failures == 0
    assert bridge._consecutive_probe_successes == 3
    assert bridge.restart_count == 0

    # 7th probe fails -> consecutive failures = 1
    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge._consecutive_probe_failures == 1
    assert bridge._consecutive_probe_successes == 0


# ---------------------------------------------------------------------------
# E6.4: Budget arithmetic constants checked at import time
# ---------------------------------------------------------------------------
def test_e64_import_time_invariants_positive() -> None:
    assert lifecycle._DEFERRAL_COUNT_CEILING > 0
    assert lifecycle._DEFERRAL_WALL_TIME_CEILING_S > 0
    assert lifecycle._TOOL_REGISTRY_MAX_RESETS > 0
    assert lifecycle._RESPAWN_ALIAS_VERIFY_TIMEOUT_S > 0

    # Pin both computed bounds (E6.4 + E2.3):
    # (a) worst-case time to terminal exhaustion with old child dead
    assert lifecycle._WORST_CASE_TERMINAL_EXHAUSTION_DEAD_CHILD_S > 0
    expected_dead_exhaustion = McpRestartPolicy.max_restarts * (
        McpRestartPolicy.cooldown_seconds + lifecycle._PREFLIGHT_DEADLINE_S
    )
    assert expected_dead_exhaustion == lifecycle._WORST_CASE_TERMINAL_EXHAUSTION_DEAD_CHILD_S

    # (b) candidate delay bound with old child alive (candidate deadline + handover ack + kill wait + drain)
    assert lifecycle._CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S > 0
    expected_alive_bound = (
        lifecycle._CANDIDATE_DEADLINE_S
        + _HANDOVER_ACK_TIMEOUT_S
        + lifecycle._HANDOVER_KILL_WAIT_S
        + _HANDOVER_DRAIN_S
    )
    assert expected_alive_bound == lifecycle._CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S

    # E2.3 candidate startup deadline bounds
    assert 0 < lifecycle._CANDIDATE_STARTUP_DEADLINE_S < lifecycle._PREFLIGHT_DEADLINE_S


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
