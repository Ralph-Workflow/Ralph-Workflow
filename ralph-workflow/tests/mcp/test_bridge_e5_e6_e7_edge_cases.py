from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import psutil
import pytest

from ralph.mcp.server import (
    McpRestartPolicy,
    StandaloneMcpProcess,
    WireLedgerIOError,
    append_wire_record,
    lifecycle,
)

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


class _FakeProcess:
    def __init__(
        self, poll_result: int | None = None, pid: int = 9999, *, unkillable: bool = False
    ) -> None:
        self._poll_result = poll_result
        self._pid = pid
        self.terminated = False
        self.killed = False
        self._unkillable = unkillable

    @property
    def pid(self) -> int:
        return self._pid

    def poll(self) -> int | None:
        return self._poll_result

    def terminate(self, grace_period_s: float = 5.0) -> None:
        self.terminated = True
        if not self._unkillable:
            self._poll_result = 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self.killed = True
        if not self._unkillable:
            self._poll_result = -9


def _make_inner(
    poll_result: int | None = None,
    pid: int = 9999,
    endpoint: str = "http://127.0.0.1:9100/mcp",
    *,
    unkillable: bool = False,
) -> StandaloneMcpProcess:
    session_file = pathlib.Path(tempfile.mkdtemp()) / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    return StandaloneMcpProcess(
        endpoint=endpoint,
        process=_FakeProcess(poll_result=poll_result, pid=pid, unkillable=unkillable),
        session_file=session_file,
    )


def test_e51_pid_reuse_guard_anchored_to_bridge_generation() -> None:
    inner = _make_inner(poll_result=None, pid=1001)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_inner(poll_result=None, pid=1001),
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
    )
    initial_gen = bridge._bridge_generation

    bridge._bridge_generation += 1

    restarted = bridge._handle_failed_probe_under_lock(
        fresh_b_signal=False,
        probe_duration_s=1.0,
        probed_pid=1001,
        probed_generation=initial_gen,
    )
    assert restarted is False
    assert bridge.restart_count == 0


def test_e52_unkillable_child_does_not_block_promotion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(lifecycle, "_HANDOVER_ACK_TIMEOUT_S", 0.05)
    monkeypatch.setattr(lifecycle, "_HANDOVER_KILL_WAIT_S", 0.05)
    monkeypatch.setattr(lifecycle, "_UNKILLABLE_REAP_BACKOFF_S", 0.0)
    monkeypatch.setattr(lifecycle, "_UNKILLABLE_REAP_RETRY_S", 0.0)

    workspace = tmp_path
    old_inner = _make_inner(poll_result=None, pid=1001, unkillable=True)
    candidate = _make_inner(poll_result=None, pid=2002)

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: candidate,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
        workspace_root=workspace,
        sleep_fn=time.sleep,
    )

    bridge._hand_over_accept(old_inner, candidate)

    assert bridge.process is old_inner.process


def test_e52_unkillable_child_promotes_via_full_make_before_break(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(lifecycle, "_HANDOVER_ACK_TIMEOUT_S", 0.05)
    monkeypatch.setattr(lifecycle, "_HANDOVER_KILL_WAIT_S", 0.05)
    monkeypatch.setattr(lifecycle, "_UNKILLABLE_REAP_BACKOFF_S", 0.0)
    monkeypatch.setattr(lifecycle, "_UNKILLABLE_REAP_RETRY_S", 0.0)

    workspace = tmp_path
    old_inner = _make_inner(poll_result=None, pid=1001, unkillable=True)
    candidate = _make_inner(poll_result=None, pid=2002)

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: candidate,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
        workspace_root=workspace,
        sleep_fn=time.sleep,
    )

    promoted = bridge._perform_restart(process_exited=False, probe_duration_s=1.0)
    assert promoted is True
    assert bridge.process is candidate.process
    assert bridge.process.pid == 2002


def test_e53_stopped_child_trips_deferral_ceiling_and_forces_replacement() -> None:
    stopped_inner = _make_inner(poll_result=None, pid=1001)
    replacement = _make_inner(poll_result=None, pid=2002)

    bridge = lifecycle.RestartAwareMcpBridge(
        stopped_inner,
        restart_fn=lambda: replacement,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
        sleep_fn=lambda s: None,
    )

    for _ in range(bridge.diagnostics().probe_deferral_count_ceiling):
        restarted = bridge._handle_failed_probe_under_lock(
            fresh_b_signal=True,
            probe_duration_s=1.0,
            probed_pid=1001,
            probed_generation=0,
        )
        assert restarted is False

    assert (
        bridge.diagnostics().deferred_probe_failures
        == bridge.diagnostics().probe_deferral_count_ceiling
    )

    restarted = bridge._handle_failed_probe_under_lock(
        fresh_b_signal=True,
        probe_duration_s=1.0,
        probed_pid=1001,
        probed_generation=0,
    )
    assert restarted is True
    assert bridge.process.pid == 2002
    assert bridge.restart_count == 1


def test_e54_zombie_child_reaped_by_poll() -> None:
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
            time.sleep(0.02)

        assert observed_zombie, "child must be observed in the OS zombie state before reaping"

        os.kill(proc.pid, 0)

        returncode = proc.poll()
        assert returncode == 0

        with pytest.raises(ProcessLookupError):
            os.kill(proc.pid, 0)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=1.0)


def test_e54_is_alive_process_uses_poll_not_bare_kill() -> None:
    live = _FakeProcess(poll_result=None, pid=1001)
    assert lifecycle.is_alive_process(live) is True

    dead = _FakeProcess(poll_result=0, pid=1001)
    assert lifecycle.is_alive_process(dead) is False

    class _NoPoll:
        pass

    assert lifecycle.is_alive_process(_NoPoll()) is False


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

    restarted = bridge._handle_failed_probe_under_lock(
        fresh_b_signal=True,
        probe_duration_s=1.0,
        probed_pid=1001,
        probed_generation=0,
    )
    assert restarted is False
    assert bridge.diagnostics().deferred_probe_failures == 1

    current_time[0] += bridge.diagnostics().probe_deferral_wall_time_ceiling_s + 10.0

    restarted = bridge._handle_failed_probe_under_lock(
        fresh_b_signal=True,
        probe_duration_s=1.0,
        probed_pid=1001,
        probed_generation=0,
    )
    assert restarted is True
    assert bridge.process.pid == 2002


def test_e62_probe_slack_bounds_classification() -> None:
    bridge = lifecycle.RestartAwareMcpBridge(
        _make_inner(poll_result=None, pid=1001),
        restart_fn=lambda: _make_inner(poll_result=None, pid=2002),
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
    )
    assert bridge.diagnostics().probe_deadline_slack_s > 0
    assert bridge.diagnostics().probe_deadline_slack_s <= 1.0


def test_e63_flapping_server_resets_consecutive_failures() -> None:
    inner = _make_inner(poll_result=None, pid=1001)
    probe_results = [True, False, True, False, False, False, True]

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

    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.diagnostics().consecutive_probe_failures == 1
    assert bridge.diagnostics().consecutive_probe_successes == 0

    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.diagnostics().consecutive_probe_failures == 1
    assert bridge.diagnostics().consecutive_probe_successes == 1

    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.diagnostics().consecutive_probe_failures == 2
    assert bridge.diagnostics().consecutive_probe_successes == 0

    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.diagnostics().consecutive_probe_failures == 2
    assert bridge.diagnostics().consecutive_probe_successes == 1

    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.diagnostics().consecutive_probe_failures == 2
    assert bridge.diagnostics().consecutive_probe_successes == 2

    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.diagnostics().consecutive_probe_failures == 0
    assert bridge.diagnostics().consecutive_probe_successes == 3
    assert bridge.restart_count == 0

    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.diagnostics().consecutive_probe_failures == 1
    assert bridge.diagnostics().consecutive_probe_successes == 0


def test_e64_import_time_invariants_positive() -> None:
    bridge = lifecycle.RestartAwareMcpBridge(
        _make_inner(poll_result=None, pid=1001),
        restart_fn=lambda: _make_inner(poll_result=None, pid=2002),
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
    )
    assert bridge.diagnostics().probe_deferral_count_ceiling > 0
    assert bridge.diagnostics().probe_deferral_wall_time_ceiling_s > 0
    assert bridge.diagnostics().tool_registry_max_resets > 0
    assert bridge.diagnostics().respawn_alias_verify_timeout_s > 0

    assert bridge.diagnostics().worst_case_terminal_exhaustion_dead_child_s > 0
    expected_dead_exhaustion = McpRestartPolicy.max_restarts * (
        McpRestartPolicy.cooldown_seconds
        + bridge.diagnostics().preflight_deadline_s
        + bridge.diagnostics().failed_attempt_cleanup_s
    )
    assert (
        expected_dead_exhaustion == bridge.diagnostics().worst_case_terminal_exhaustion_dead_child_s
    )

    assert bridge.diagnostics().candidate_delay_bound_alive_child_s > 0
    expected_alive_bound = (
        bridge.diagnostics().candidate_deadline_s
        + bridge.diagnostics().handover_ack_timeout_s
        + bridge.diagnostics().handover_kill_wait_s
        + bridge.diagnostics().handover_drain_s
        + bridge.diagnostics().failed_attempt_cleanup_s
    )
    assert expected_alive_bound == bridge.diagnostics().candidate_delay_bound_alive_child_s

    assert (
        0
        < bridge.diagnostics().candidate_startup_deadline_s
        < bridge.diagnostics().preflight_deadline_s
    )


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


def test_e73_contention_signal_defers_restart() -> None:
    old_inner = _make_inner(poll_result=None, pid=1001)
    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: _make_inner(poll_result=None, pid=2002),
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
        contention_signal_producer=lambda: True,
    )

    restarted = bridge._handle_failed_probe_under_lock(
        fresh_b_signal=True,
        probe_duration_s=1.0,
        probed_pid=1001,
        probed_generation=0,
    )
    assert restarted is False
    assert bridge.diagnostics().deferred_probe_failures == 1
    assert bridge.restart_count == 0
    assert bridge.process.pid == 1001
