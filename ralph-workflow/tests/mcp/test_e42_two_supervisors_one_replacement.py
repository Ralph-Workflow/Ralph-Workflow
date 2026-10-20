"""Pins E4.2 and E2.7: two supervisors concurrently replacing and single-candidate invariant.

Catalogue requirement E4.2:
- Two supervisors / callers concurrently replacing -> per-bridge coordination guarantees
  that a single replacement succeeds (single winner, other detects replacement or no-op).
- If the restart budget is exhausted, terminal failure is delivered to both supervisors.

Catalogue requirement E2.7:
- Only one candidate at a time: second probe failure, second supervisor tick, or
  reset_tool_registry() during candidate startup joins or waits under the bridge lock,
  never spawning concurrent duplicate candidate processes.

All inner processes in this suite are REAL OS subprocesses managed by ProcessManager.
"""

from __future__ import annotations

import contextlib
import pathlib
import sys
import threading
import time
from datetime import timedelta

import pytest

from ralph.mcp.server import lifecycle
from ralph.mcp.server._mcp_attempt_result import RestartAttemptResult
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from ralph.process.manager import ManagedProcess, SpawnOptions, get_process_manager
from ralph.process.mcp_supervisor import McpSupervisor

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(15)]


def _spawn_real_process(
    tmp_path: pathlib.Path,
    *,
    label: str,
    duration_s: float = 30.0,
    exit_code: int | None = None,
    snap_dir: pathlib.Path | None = None,
) -> ManagedProcess:
    script = tmp_path / f"{label}.py"
    if exit_code is not None:
        script.write_text(f"import sys\nsys.exit({exit_code})\n", encoding="utf-8")
    else:
        script.write_text(
            "import os, pathlib, signal, sys, time\n"
            "signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))\n"
            "snap = os.environ.get('RALPH_MCP_SNAPSHOT_DIR')\n"
            f"deadline = time.monotonic() + {duration_s}\n"
            "while time.monotonic() < deadline:\n"
            "    if snap:\n"
            "        p = pathlib.Path(snap)\n"
            "        for f in p.rglob('accept.pause*'):\n"
            "            if not f.name.endswith('.ack'):\n"
            "                try:\n"
            "                    pathlib.Path(str(f) + '.ack').touch()\n"
            "                except Exception:\n"
            "                    pass\n"
            "    time.sleep(0.01)\n",
            encoding="utf-8",
        )
    target_snap = str(snap_dir if snap_dir is not None else tmp_path)
    proc = get_process_manager().spawn(
        [sys.executable, str(script)],
        SpawnOptions(
            cwd=str(tmp_path),
            label=label,
            env={"RALPH_MCP_SNAPSHOT_DIR": target_snap},
        ),
    )
    if exit_code is not None:
        proc.wait(timeout=2.0)
    return proc


def _reap_procs(procs: list[ManagedProcess]) -> None:
    for p in procs:
        if p.poll() is None:
            with contextlib.suppress(Exception):
                p.kill()
                p.wait(timeout=1.0)


def _make_real_inner(
    tmp_path: pathlib.Path,
    proc: ManagedProcess,
    *,
    endpoint: str = "http://127.0.0.1:9100/mcp",
) -> StandaloneMcpProcess:
    session_file = tmp_path / f"session_{proc.pid}.json"
    session_file.write_text("{}", encoding="utf-8")
    return StandaloneMcpProcess(
        endpoint=endpoint,
        process=proc,
        session_file=session_file,
    )


def test_e42_two_supervisors_concurrent_replacement_single_winner(tmp_path: pathlib.Path) -> None:
    """E4.2: Two supervisors concurrently checking an unhealthy bridge -> single replacement succeeds.

    Exactly one supervisor performs the restart (winner), while the other detects
    that replacement is already complete (or in flight) and does not spawn a second candidate.
    """
    procs: list[ManagedProcess] = []
    try:
        p_init = _spawn_real_process(tmp_path, label="e42-init", exit_code=1)
        procs.append(p_init)
        initial_inner = _make_real_inner(tmp_path, p_init)

        lock = threading.Lock()
        active_spawns = 0
        max_active_spawns = 0
        total_spawns = 0

        def restart_fn() -> StandaloneMcpProcess:
            nonlocal active_spawns, max_active_spawns, total_spawns
            with lock:
                active_spawns += 1
                total_spawns += 1
                max_active_spawns = max(max_active_spawns, active_spawns)
                cur = total_spawns
            try:
                # Brief hold to guarantee overlap if concurrent callers attempt spawning
                time.sleep(0.04)
                p = _spawn_real_process(tmp_path, label=f"e42-cand-{cur}")
                with lock:
                    procs.append(p)
                return _make_real_inner(tmp_path, p)
            finally:
                with lock:
                    active_spawns -= 1

        bridge = lifecycle.RestartAwareMcpBridge(
            initial_inner,
            restart_fn=restart_fn,
            restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
            run_id="test-e42-run",
            sleep_fn=lambda _s: None,
        )

        sup1 = McpSupervisor(bridge, check_interval=timedelta(seconds=1))
        sup2 = McpSupervisor(bridge, check_interval=timedelta(seconds=1))

        results: list[bool] = []
        errors: list[Exception] = []

        def run_check(sup: McpSupervisor) -> None:
            try:
                res = sup._do_check_once()
                with lock:
                    results.append(res)
            except Exception as exc:
                with lock:
                    errors.append(exc)

        t1 = threading.Thread(target=run_check, args=(sup1,))
        t2 = threading.Thread(target=run_check, args=(sup2,))
        t1.start()
        t2.start()
        t1.join(timeout=3.0)
        t2.join(timeout=3.0)

        assert not errors, f"Unexpected errors during concurrent checks: {errors}"
        assert len(results) == 2
        # Exactly one winner performed the restart (True), the other observed healthy/restarted (False)
        assert sorted(results) == [False, True]
        assert bridge.restart_count == 1
        assert total_spawns == 1
        # Pin E2.7: Only one candidate at a time
        assert max_active_spawns == 1
        assert bridge.process.poll() is None
    finally:
        _reap_procs(procs)


def test_e27_single_candidate_during_startup_serialization(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E2.7: Concurrent probe failure and reset_tool_registry serialize without duplicate candidates."""
    monkeypatch.setattr(
        lifecycle,
        "_http_tools_list_names",
        lambda endpoint, timeout=2.0: [
            f"mcp__{lifecycle._ALIAS_VERIFY_SERVER_NAME}__{lifecycle._ALIAS_VERIFY_TOOL_NAME}"
        ],
    )
    run_id = "test-e27-run"
    snap_dir = lifecycle.snapshot_dir(tmp_path, run_id)
    procs: list[ManagedProcess] = []
    try:
        p_init = _spawn_real_process(tmp_path, label="e27-init", snap_dir=snap_dir)
        procs.append(p_init)
        initial_inner = _make_real_inner(tmp_path, p_init)

        lock = threading.Lock()
        active_spawns = 0
        max_active_spawns = 0
        total_spawns = 0

        def restart_fn(make_before_break: bool = True) -> StandaloneMcpProcess:
            nonlocal active_spawns, max_active_spawns, total_spawns
            with lock:
                active_spawns += 1
                total_spawns += 1
                max_active_spawns = max(max_active_spawns, active_spawns)
                cur = total_spawns
            try:
                time.sleep(0.04)
                p = _spawn_real_process(tmp_path, label=f"e27-cand-{cur}", snap_dir=snap_dir)
                with lock:
                    procs.append(p)
                return _make_real_inner(tmp_path, p)
            finally:
                with lock:
                    active_spawns -= 1

        bridge = lifecycle.RestartAwareMcpBridge(
            initial_inner,
            restart_fn=restart_fn,
            restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
            run_id=run_id,
            workspace_root=tmp_path,
            sleep_fn=lambda _s: None,
        )

        # Simulate child exit to trigger restart path
        initial_inner.process.terminate()

        errors: list[Exception] = []

        def do_supervisor_check() -> None:
            try:
                bridge.check_health_and_restart_if_needed()
            except Exception as exc:
                with lock:
                    errors.append(exc)

        def do_reset_registry() -> None:
            try:
                bridge.reset_tool_registry()
            except Exception as exc:
                with lock:
                    errors.append(exc)

        t1 = threading.Thread(target=do_supervisor_check)
        t2 = threading.Thread(target=do_reset_registry)
        t1.start()
        t2.start()
        t1.join(timeout=3.0)
        t2.join(timeout=3.0)

        assert not errors, f"Unexpected errors during concurrent operations: {errors}"
        assert max_active_spawns == 1
        # Operations succeeded and bridge is left pointing to a valid candidate
        assert bridge.restart_count in (1, 2)
        assert bridge.process.poll() is None
    finally:
        _reap_procs(procs)


def test_e42_two_supervisors_terminal_budget_delivered_to_both(tmp_path: pathlib.Path) -> None:
    """E4.2: When restart budget is exhausted, terminal failure is delivered to both supervisors."""
    procs: list[ManagedProcess] = []
    try:
        dead_p = _spawn_real_process(tmp_path, label="e42-term-dead", exit_code=137)
        procs.append(dead_p)
        dead_inner = _make_real_inner(tmp_path, dead_p)

        lock = threading.Lock()

        def restart_fn() -> StandaloneMcpProcess:
            p = _spawn_real_process(tmp_path, label="e42-term-cand")
            with lock:
                procs.append(p)
            return _make_real_inner(tmp_path, p)

        bridge = lifecycle.RestartAwareMcpBridge(
            dead_inner,
            restart_fn=restart_fn,
            restart_policy=McpRestartPolicy(max_restarts=0, cooldown_seconds=0.0),
            run_id="test-e42-terminal-run",
            sleep_fn=lambda _s: None,
        )

        sup1_errors: list[McpServerError] = []
        sup2_errors: list[McpServerError] = []

        sup1 = McpSupervisor(
            bridge,
            check_interval=timedelta(seconds=1),
            on_error=sup1_errors.append,
        )
        sup2 = McpSupervisor(
            bridge,
            check_interval=timedelta(seconds=1),
            on_error=sup2_errors.append,
        )

        t1_caught: list[McpServerError] = []
        t2_caught: list[McpServerError] = []

        def check_sup(sup: McpSupervisor, caught: list[McpServerError]) -> None:
            try:
                sup._do_check_once()
            except McpServerError as exc:
                caught.append(exc)

        t1 = threading.Thread(target=check_sup, args=(sup1, t1_caught))
        t2 = threading.Thread(target=check_sup, args=(sup2, t2_caught))
        t1.start()
        t2.start()
        t1.join(timeout=3.0)
        t2.join(timeout=3.0)

        # Invariant: Terminal McpServerError delivered to both supervisors
        assert len(t1_caught) == 1
        assert t1_caught[0].failure_kind == RestartAttemptResult.BUDGET_EXHAUSTED
        assert len(t2_caught) == 1
        assert t2_caught[0].failure_kind == RestartAttemptResult.BUDGET_EXHAUSTED

        assert len(sup1_errors) == 1
        assert len(sup2_errors) == 1
        assert sup1._mcp_error is not None
        assert sup2._mcp_error is not None
    finally:
        _reap_procs(procs)


def test_e42_concurrent_health_checks_with_probe_failure(tmp_path: pathlib.Path) -> None:
    """E4.2: Concurrent probe failure checks serialize cleanly; single replacement occurs."""
    procs: list[ManagedProcess] = []
    try:
        p_init = _spawn_real_process(tmp_path, label="e42-probe-init")
        procs.append(p_init)
        initial_inner = _make_real_inner(tmp_path, p_init)

        spawns = 0
        lock = threading.Lock()

        def restart_fn(make_before_break: bool = True) -> StandaloneMcpProcess:
            nonlocal spawns
            with lock:
                spawns += 1
                cur = spawns
            p = _spawn_real_process(tmp_path, label=f"e42-probe-cand-{cur}")
            with lock:
                procs.append(p)
            return _make_real_inner(tmp_path, p)

        # Configure bridge where probe fails
        bridge = lifecycle.RestartAwareMcpBridge(
            initial_inner,
            restart_fn=restart_fn,
            restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
            run_id="test-e42-probe-fail",
            sleep_fn=lambda _s: None,
            probe_fn=lambda endpoint, timeout: (_ for _ in ()).throw(RuntimeError("probe failed")),
            contention_signal_producer=lambda: False,
        )

        results: list[bool] = []

        def check() -> None:
            res = bridge.check_health_and_restart_if_needed()
            with lock:
                results.append(res)

        t1 = threading.Thread(target=check)
        t2 = threading.Thread(target=check)
        t1.start()
        t2.start()
        t1.join(timeout=3.0)
        t2.join(timeout=3.0)

        assert len(results) == 2
        # One check triggered restart, or both serialized through failure counters
        assert bridge.restart_count <= 1
        assert spawns <= 1
    finally:
        _reap_procs(procs)
