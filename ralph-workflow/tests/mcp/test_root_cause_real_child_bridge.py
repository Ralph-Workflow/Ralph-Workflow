from __future__ import annotations

import contextlib
import errno
import functools
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path

import psutil
import pytest

from ralph.mcp.protocol.env import (
    MCP_STAGE_ID_ENV,
)
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle
from ralph.mcp.server._handover_protocol import (
    check_candidate_readiness,
    compute_tool_set_hash,
    create_handover_socketpair,
    verify_candidate_readiness,
)
from ralph.mcp.server._http_tools_list import _http_tools_list_names
from ralph.mcp.server._mcp_attempt_result import RestartAttemptResult
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._snapshots import snapshot_dir
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from ralph.mcp.server._start_stage_tracker import read_last_stage
from ralph.process.manager import ManagedProcess, SpawnOptions, get_process_manager
from ralph.workspace.fs import FsWorkspace

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.timeout_seconds(10),
    pytest.mark.xdist_group("mcp_root_cause"),
]


def _descendant_pids() -> set[int]:
    return {d.pid for d in psutil.Process(os.getpid()).children(recursive=True)}


@pytest.fixture
def managed_spawner(tmp_path: Path) -> Iterator[Callable[..., ManagedProcess]]:
    spawned: list[ManagedProcess] = []
    pm = get_process_manager()

    def _spawn(
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        pass_fds: tuple[int, ...] = (),
        label: str = "test-child",
        stdout: int | None = None,
        stderr: int | None = None,
    ) -> ManagedProcess:
        proc = pm.spawn(
            args,
            SpawnOptions(
                cwd=str(tmp_path),
                env=env,
                pass_fds=pass_fds,
                label=label,
                stdout=stdout,
                stderr=stderr,
            ),
        )
        spawned.append(proc)
        return proc

    try:
        yield _spawn
    finally:
        for p in spawned:
            if p.poll() is None:
                with contextlib.suppress(Exception):
                    p.kill()
                with contextlib.suppress(Exception):
                    p.wait(timeout=2.0)


_CHILD_SERVING_TIMEOUT_S = 15.0


def _wait_child_serving(endpoint: str) -> list[str]:
    return _http_tools_list_names(endpoint, timeout=_CHILD_SERVING_TIMEOUT_S)


def _write_responder_script(tmp_path: Path) -> Path:
    script_path = tmp_path / "readiness_child.py"
    if not script_path.exists():
        script = """import json
import os
import socket
import subprocess
import sys

sock_fd = int(sys.argv[1])
tools_json = sys.argv[2]
tools = json.loads(tools_json)
grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
sock = socket.socket(fileno=sock_fd)
sock.settimeout(15.0)
buf = b""
while True:
    try:
        chunk = sock.recv(4096)
    except (TimeoutError, OSError):
        break
    if not chunk:
        break
    buf += chunk
    while b"\\n" in buf:
        line, buf = buf.split(b"\\n", 1)
        line_str = line.strip().decode("utf-8")
        if not line_str:
            continue
        req = json.loads(line_str)
        msg_id = req.get("id")
        method = req.get("method")
        if method == "initialize":
            res = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "pid": os.getpid(),
                    "spawn_id": os.environ.get("RALPH_MCP_STAGE_ID", ""),
                    "session_id": "",
                    "broker_secret_fingerprint": None,
                },
            }
        elif method == "tools/list":
            res = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": tools},
            }
        else:
            res = {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        sock.sendall(json.dumps(res).encode("utf-8") + b"\\n")
"""
        script_path.write_text(script, encoding="utf-8")
    return script_path


def _make_real_standalone(
    proc: ManagedProcess,
    tmp_path: Path,
    *,
    stage_id: str | None = None,
    handover_parent_sock: socket.socket | None = None,
    port: int = 9100,
) -> StandaloneMcpProcess:
    session_file = tmp_path / f"session_{uuid.uuid4().hex[:8]}.json"
    session_file.write_text("{}", encoding="utf-8")
    return StandaloneMcpProcess(
        endpoint=f"http://127.0.0.1:{port}/mcp",
        process=proc,
        session_file=session_file,
        stage_id=stage_id,
        handover_parent_sock=handover_parent_sock,
    )


def test_c1_tool_hash_mismatch_rejected_real_child(
    managed_spawner: Callable[..., ManagedProcess],
    tmp_path: Path,
) -> None:
    old_proc = managed_spawner(
        ["sleep", "300"],
        label="c1-old-child",
    )
    old_inner = _make_real_standalone(old_proc, tmp_path, stage_id="old-stage-c1")

    tools_old = [{"name": "read_file", "description": "read", "inputSchema": {}}]
    tools_new = [{"name": "write_file", "description": "write", "inputSchema": {}}]
    expected_hash = compute_tool_set_hash(tools_old)
    drifted_hash = compute_tool_set_hash(tools_new)
    assert expected_hash != drifted_hash

    responder_script = _write_responder_script(tmp_path)
    cand_procs: list[ManagedProcess] = []

    def make_candidate() -> StandaloneMcpProcess:
        parent_sock, child_sock = create_handover_socketpair()
        fd = child_sock.fileno()
        cand_stage = "cand-stage-drift"
        cand_proc = managed_spawner(
            [sys.executable, str(responder_script), str(fd), json.dumps(tools_new)],
            env={"RALPH_MCP_STAGE_ID": cand_stage},
            pass_fds=(fd,),
            label="c1-cand-child",
        )
        child_sock.close()
        cand_procs.append(cand_proc)
        return _make_real_standalone(
            cand_proc,
            tmp_path,
            stage_id=cand_stage,
            handover_parent_sock=parent_sock,
        )

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=make_candidate,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id=f"test-run-c1-drift-{uuid.uuid4().hex[:8]}",
        sleep_fn=lambda s: None,
        workspace_root=tmp_path,
    )
    bridge._expected_tool_hash = expected_hash
    bridge._expected_tool_entries = tools_old

    result = bridge._run_with_make_before_break()

    assert result is False
    assert len(cand_procs) == 1
    cand_proc = cand_procs[0]

    assert bridge.process.pid == old_proc.pid
    assert old_proc.poll() is None

    cand_proc.wait(timeout=2.0)
    assert cand_proc.poll() is not None


@pytest.mark.timeout_seconds(60)
def test_c1_matching_tool_hash_promoted_and_private_hash_equals_served_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AgentSession(
        session_id=f"session-c1-prom-{uuid.uuid4().hex[:8]}",
        run_id=f"run-c1-prom-{uuid.uuid4().hex[:8]}",
        drain="development",
        capabilities={"WorkspaceRead"},
    )
    workspace = FsWorkspace(tmp_path)
    deps = lifecycle._default_lifecycle_deps()
    monkeypatch.setattr(
        lifecycle,
        "check_candidate_readiness",
        functools.partial(check_candidate_readiness, timeout=20.0),
    )
    extras = lifecycle.McpServerExtras(
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
    )

    bridge = lifecycle.start_mcp_server(session, workspace, deps=deps, extras=extras)
    try:
        old_pid = bridge.process.pid
        endpoint = bridge.endpoint_uri()

        stop_burst = threading.Event()
        burst_results: list[bool] = []
        burst_errors: list[Exception] = []

        def _burst_worker() -> None:
            while not stop_burst.is_set():
                try:
                    names = _http_tools_list_names(endpoint, timeout=5.0)
                    if isinstance(names, list) and len(names) > 0:
                        burst_results.append(True)
                    else:
                        burst_results.append(False)
                except Exception as exc:
                    burst_errors.append(exc)
                time.sleep(0.015)

        burst_thread = threading.Thread(target=_burst_worker, daemon=True)
        burst_thread.start()

        promoted = bridge._perform_restart(process_exited=False, probe_duration_s=0.1)

        stop_burst.set()
        burst_thread.join(timeout=5.0)

        assert promoted is True, "Candidate with matching hash must be promoted"
        new_pid = bridge.process.pid
        assert new_pid != old_pid, f"PID must change on promotion: old={old_pid}, new={new_pid}"
        assert bridge.restart_count == 1

        assert len(burst_errors) == 0, f"tools/list burst encountered errors: {burst_errors}"
        assert len(burst_results) > 0, "tools/list burst must have executed at least one request"
        assert all(burst_results), "Every tools/list request during handover must succeed"

        final_names = _http_tools_list_names(endpoint, timeout=5.0)
        assert isinstance(final_names, list)
        assert len(final_names) > 0
    finally:
        with contextlib.suppress(Exception):
            bridge.shutdown()


def test_c1_preflight_identity_metadata_mismatch_rejected(
    managed_spawner: Callable[..., ManagedProcess],
    tmp_path: Path,
) -> None:
    parent_sock, child_sock = create_handover_socketpair()
    fd = child_sock.fileno()

    mismatch_script = tmp_path / "mismatch_child.py"
    mismatch_script.write_text(
        """import json, os, socket, sys
sock_fd = int(sys.argv[1])
sock = socket.socket(fileno=sock_fd)
buf = b""
while True:
    chunk = sock.recv(4096)
    if not chunk: break
    buf += chunk
    while b"\\n" in buf:
        line, buf = buf.split(b"\\n", 1)
        req = json.loads(line.decode("utf-8"))
        if req["method"] == "initialize":
            res = {"jsonrpc": "2.0", "id": req["id"], "result": {
                "pid": 999999,  # Mismatched PID
                "spawn_id": "wrong-spawn-id",
                "session_id": "",
                "broker_secret_fingerprint": None,
            }}
            sock.sendall(json.dumps(res).encode("utf-8") + b"\\n")
""",
        encoding="utf-8",
    )

    proc = managed_spawner(
        [sys.executable, str(mismatch_script), str(fd)],
        pass_fds=(fd,),
        label="c1-mismatch-child",
    )
    child_sock.close()

    ok, stage, err = verify_candidate_readiness(
        parent_sock,
        expected_spawn_id="expected-stage-id",
        expected_pid=proc.pid,
        expected_session_id="",
        expected_broker_fingerprint=None,
    )
    parent_sock.close()

    assert ok is False
    assert stage == "pid_mismatch"
    assert "999999" in str(err)


def test_c2_child_stdout_stderr_buffering_and_pipe_hygiene(
    managed_spawner: Callable[..., ManagedProcess],
) -> None:
    pipe_script = """import sys
data = b"D" * (128 * 1024)
sys.stdout.buffer.write(data)
sys.stdout.buffer.flush()
sys.stderr.buffer.write(data)
sys.stderr.buffer.flush()
"""
    proc = managed_spawner(
        [sys.executable, "-c", pipe_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        label="c2-pipe-child",
    )

    t0 = time.monotonic()
    stdout, stderr = proc.communicate(timeout=5.0)
    elapsed = time.monotonic() - t0

    assert elapsed < 5.0
    assert stdout is not None and len(stdout) == 128 * 1024
    assert stderr is not None and len(stderr) == 128 * 1024
    assert proc.poll() == 0


@pytest.mark.timeout_seconds(30)
def test_c2_failed_candidate_survival_and_single_failure_not_terminal(
    tmp_path: Path,
) -> None:
    session = AgentSession(
        session_id="session-c2-survival-real",
        run_id="run-c2-survival-real",
        drain="development",
        capabilities={"WorkspaceRead"},
    )
    workspace = FsWorkspace(tmp_path)

    real_spawn = lifecycle._default_lifecycle_deps().spawn_process

    def noop_preflight(endpoint: str, required_tools: list[str], timeout: timedelta) -> None:
        del endpoint, required_tools, timeout

    deps = lifecycle._default_lifecycle_deps().__replace__(
        preflight=noop_preflight, spawn_process=real_spawn
    )
    extras = lifecycle.McpServerExtras(
        restart_policy=McpRestartPolicy(cooldown_seconds=0.0),
    )

    preexisting_pids = _descendant_pids()
    bridge = lifecycle.start_mcp_server(session, workspace, deps=deps, extras=extras)

    try:
        old_pid = bridge.process.pid
        assert old_pid > 0
        assert bridge.process.poll() is None

        endpoint = bridge.endpoint_uri()

        names_before = _wait_child_serving(endpoint)
        assert isinstance(names_before, list)

        def failing_candidate_spawn() -> StandaloneMcpProcess:
            raise RuntimeError("simulated candidate spawn failure")

        bridge._restart_fn = failing_candidate_spawn

        promoted = bridge._perform_restart(process_exited=False, probe_duration_s=0.5)

        assert promoted is False, "failed candidate spawn must not report promotion"
        assert bridge.restart_count == 1, "failed attempt counts once toward budget"
        assert bridge.process.pid == old_pid, "old child PID must remain _inner"
        assert bridge._inner is not None
        old_inner_proc = bridge._inner.process
        assert old_inner_proc.poll() is None, "old child must remain alive"

        names_after = _http_tools_list_names(endpoint, timeout=2.0)
        assert isinstance(names_after, list)
        assert names_after == names_before, (
            "tools/list response must be identical before and after the failed "
            "restart — the old child was never replaced"
        )

        descendants_before = _descendant_pids() - preexisting_pids - {old_pid}
        assert descendants_before == set(), (
            f"failed candidate spawn leaked unrelated child PIDs: {descendants_before}"
        )

    finally:
        with contextlib.suppress(Exception):
            bridge.shutdown()


@pytest.mark.timeout_seconds(30)
def test_c2_live_old_child_serves_tools_list_during_failed_candidate(
    tmp_path: Path,
) -> None:
    session = AgentSession(
        session_id="session-c2-live-serving",
        run_id="run-c2-live-serving",
        drain="development",
        capabilities={"WorkspaceRead"},
    )
    workspace = FsWorkspace(tmp_path)

    real_spawn = lifecycle._default_lifecycle_deps().spawn_process

    def noop_preflight(endpoint: str, required_tools: list[str], timeout: timedelta) -> None:
        del endpoint, required_tools, timeout

    deps = lifecycle._default_lifecycle_deps().__replace__(
        preflight=noop_preflight, spawn_process=real_spawn
    )
    extras = lifecycle.McpServerExtras(
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
    )

    preexisting_pids = _descendant_pids()
    bridge = lifecycle.start_mcp_server(session, workspace, deps=deps, extras=extras)

    try:
        endpoint = bridge.endpoint_uri()
        assert bridge.process.poll() is None
        live_pid = bridge.process.pid
        assert isinstance(_wait_child_serving(endpoint), list)

        attempts = [0]

        def failing_spawn() -> StandaloneMcpProcess:
            attempts[0] += 1
            raise RuntimeError("candidate spawn wedged")

        bridge._restart_fn = failing_spawn

        first = bridge._perform_restart(process_exited=False, probe_duration_s=0.5)
        assert first is False
        assert bridge.restart_count == 1
        assert bridge.process.pid == live_pid
        assert bridge.process.poll() is None

        second = bridge._perform_restart(process_exited=False, probe_duration_s=0.5)
        assert second is False
        assert bridge.restart_count == 2
        assert bridge.process.pid == live_pid
        assert bridge.process.poll() is None

        names = _http_tools_list_names(endpoint, timeout=2.0)
        assert isinstance(names, list)

        port = int(endpoint.split(":")[-1].split("/")[0])
        foreign = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(OSError) as exc_info:
                foreign.bind(("127.0.0.1", port))
            assert exc_info.value.errno == errno.EADDRINUSE
        finally:
            foreign.close()

        descendants = _descendant_pids() - preexisting_pids
        assert live_pid in descendants, "old child must be alive under the bridge"
        candidate_pids = descendants - {live_pid}
        assert candidate_pids == set(), f"failed candidate spawned leaked PIDs: {candidate_pids}"

        assert attempts[0] == 2, "restart_fn must have been invoked twice"

    finally:
        with contextlib.suppress(Exception):
            bridge.shutdown()


def test_c2_budget_exhaustion_with_dead_old_child(
    managed_spawner: Callable[..., ManagedProcess],
    tmp_path: Path,
) -> None:
    dead_proc = managed_spawner(
        ["true"],
        label="c2-dead-child",
    )
    dead_proc.wait(timeout=2.0)
    assert dead_proc.poll() is not None

    dead_inner = _make_real_standalone(dead_proc, tmp_path, stage_id="dead-stage")

    def failing_restart() -> StandaloneMcpProcess:
        raise RuntimeError("persistent restart failure")

    max_restarts = 3
    bridge = lifecycle.RestartAwareMcpBridge(
        dead_inner,
        restart_fn=failing_restart,
        restart_policy=McpRestartPolicy(max_restarts=max_restarts, cooldown_seconds=0.0),
        run_id="test-run-c2-budget",
        sleep_fn=lambda s: None,
    )

    t0 = time.monotonic()
    for _ in range(max_restarts):
        res = bridge._attempt_restart_with_candidate(process_exited=True, probe_duration_s=None)
        assert res == RestartAttemptResult.ATTEMPT_FAILED

    with pytest.raises(McpServerError) as exc_info:
        bridge._attempt_restart_with_candidate(process_exited=True, probe_duration_s=None)

    elapsed = time.monotonic() - t0
    assert elapsed < 3.0
    assert "budget exhausted" in str(exc_info.value).lower()
    assert exc_info.value.restart_count == max_restarts


def test_c3_timeout_only_deferral_vs_connection_reset(
    managed_spawner: Callable[..., ManagedProcess],
    tmp_path: Path,
) -> None:
    proc = managed_spawner(
        ["sleep", "300"],
        label="c3-probe-child",
    )
    inner = _make_real_standalone(proc, tmp_path, stage_id="c3-probe-stage")

    def timing_out_probe(endpoint: str, timeout: timedelta) -> None:
        raise TimeoutError("probe timed out")

    bridge_timeout = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-c3-timeout",
        probe_fn=timing_out_probe,
        contention_signal_producer=lambda: True,
        sleep_fn=lambda s: None,
    )
    restarted = bridge_timeout.check_health_and_restart_if_needed()
    assert restarted is False
    assert bridge_timeout._consecutive_probe_failures_deferred == 1
    assert proc.poll() is None

    def reset_probe(endpoint: str, timeout: timedelta) -> None:
        raise ConnectionResetError("Connection reset by peer")

    restarted_called = False

    def restart_target() -> StandaloneMcpProcess:
        nonlocal restarted_called
        restarted_called = True
        return inner

    bridge_reset = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=restart_target,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-c3-reset",
        probe_fn=reset_probe,
        contention_signal_producer=lambda: True,
        sleep_fn=lambda s: None,
    )

    bridge_reset.check_health_and_restart_if_needed()
    bridge_reset.check_health_and_restart_if_needed()
    bridge_reset.check_health_and_restart_if_needed()
    assert bridge_reset._consecutive_probe_failures_deferred == 0
    assert restarted_called is True


def test_c3_confirm_before_replace_and_probe_overrun(
    managed_spawner: Callable[..., ManagedProcess],
    tmp_path: Path,
) -> None:
    proc = managed_spawner(
        ["sleep", "300"],
        label="c3-confirm-child",
    )
    inner = _make_real_standalone(proc, tmp_path, stage_id="c3-confirm-stage")

    probe_attempts = 0

    def probe_fn(endpoint: str, timeout: timedelta) -> None:
        nonlocal probe_attempts
        probe_attempts += 1
        if probe_attempts <= 3:
            raise TimeoutError("ordinary probe timed out")

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-c3-confirm",
        probe_fn=probe_fn,
        contention_signal_producer=lambda: False,
        sleep_fn=lambda s: None,
    )

    bridge.check_health_and_restart_if_needed()
    bridge.check_health_and_restart_if_needed()
    restarted = bridge.check_health_and_restart_if_needed()

    assert restarted is False
    assert bridge._consecutive_probe_failures_deferred == 1
    assert proc.poll() is None


def test_c3_deferral_ceiling_and_dead_child_never_deferred(
    managed_spawner: Callable[..., ManagedProcess],
    tmp_path: Path,
) -> None:
    proc = managed_spawner(
        ["sleep", "300"],
        label="c3-ceiling-child",
    )
    inner = _make_real_standalone(proc, tmp_path, stage_id="c3-ceiling-stage")

    restart_count = 0

    def restart_target() -> StandaloneMcpProcess:
        nonlocal restart_count
        restart_count += 1
        return inner

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=restart_target,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-c3-ceiling",
        probe_fn=lambda ep, to: (_ for _ in ()).throw(TimeoutError("timeout")),
        contention_signal_producer=lambda: True,
        sleep_fn=lambda s: None,
    )

    bridge._consecutive_probe_failures_deferred = lifecycle._DEFERRAL_COUNT_CEILING
    bridge.check_health_and_restart_if_needed()
    assert restart_count == 1

    proc.kill()
    proc.wait(timeout=2.0)
    assert proc.poll() is not None

    bridge_dead = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=restart_target,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-c3-dead",
        contention_signal_producer=lambda: True,
        sleep_fn=lambda s: None,
    )
    bridge_dead.check_health_and_restart_if_needed()
    assert bridge_dead._consecutive_probe_failures_deferred == 0


def test_c3_e53_sigstopped_child_detected_and_reaped(
    managed_spawner: Callable[..., ManagedProcess],
    tmp_path: Path,
) -> None:
    proc = managed_spawner(
        ["sleep", "300"],
        label="c3-sigstop-child",
    )
    inner = _make_real_standalone(proc, tmp_path, stage_id="c3-sigstop-stage")

    os.kill(proc.pid, signal.SIGSTOP)
    stopped = psutil.Process(proc.pid)
    deadline = time.monotonic() + 2.0
    while stopped.status() != psutil.STATUS_STOPPED and time.monotonic() < deadline:
        time.sleep(0)
    assert stopped.status() == psutil.STATUS_STOPPED

    try:
        bridge = lifecycle.RestartAwareMcpBridge(
            inner,
            restart_fn=lambda: inner,
            restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
            run_id="test-c3-sigstop",
            sleep_fn=lambda s: None,
        )

        prog = bridge._check_child_cpu_progress(proc.pid, sample_duration_s=0.01)
        assert prog is False
    finally:
        with contextlib.suppress(Exception):
            os.kill(proc.pid, signal.SIGCONT)
        proc.kill()
        proc.wait(timeout=2.0)


def test_c4_file_based_pause_ack_resume_handover_protocol(
    managed_spawner: Callable[..., ManagedProcess],
    tmp_path: Path,
) -> None:
    run_id = f"test-c4-handover-{uuid.uuid4().hex[:8]}"
    snap_dir = snapshot_dir(tmp_path, run_id)
    pause_file = snap_dir / "accept.pause.old-c4"
    resume_file = snap_dir / "accept.resume.cand-c4"

    old_ready = tmp_path / "old_child_c4.ready"
    old_script = tmp_path / "old_child_c4.py"
    old_script.write_text(
        """import time, sys, pathlib
pause_file = pathlib.Path(sys.argv[1])
ready_file = pathlib.Path(sys.argv[2])
ready_file.write_text("READY\\n", encoding="utf-8")
while True:
    if pause_file.exists():
        ack_file = pause_file.with_name(pause_file.name + ".ack")
        ack_file.write_text("ACK\\n", encoding="utf-8")
        break
    time.sleep(0.02)
while True:
    time.sleep(0.1)
""",
        encoding="utf-8",
    )

    old_proc = managed_spawner(
        [sys.executable, str(old_script), str(pause_file), str(old_ready)],
        label="c4-old-child",
    )
    old_inner = _make_real_standalone(old_proc, tmp_path, stage_id="old-c4")

    ready_deadline = time.monotonic() + 5.0
    while time.monotonic() < ready_deadline and not old_ready.exists():
        time.sleep(0.02)
    assert old_ready.exists(), "old child never reached its pause-watch loop"

    cand_script = tmp_path / "cand_child_c4.py"
    cand_script.write_text(
        """import time, sys, pathlib
resume_file = pathlib.Path(sys.argv[1])
while True:
    if resume_file.exists():
        break
    time.sleep(0.02)
while True:
    time.sleep(0.1)
""",
        encoding="utf-8",
    )

    cand_proc = managed_spawner(
        [sys.executable, str(cand_script), str(resume_file)],
        label="c4-cand-child",
    )
    cand_inner = _make_real_standalone(cand_proc, tmp_path, stage_id="cand-c4")

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: cand_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id=run_id,
        workspace_root=tmp_path,
    )

    bridge._hand_over_accept(old_inner, cand_inner)

    ack_file = pause_file.with_name(pause_file.name + ".ack")
    assert ack_file.exists()
    assert resume_file.exists()
    assert old_proc.poll() is None, "old child was killed: the ack was not observed"


def test_c5_enriched_diagnostic_reporting_abnormal_termination(
    managed_spawner: Callable[..., ManagedProcess],
    tmp_path: Path,
) -> None:
    spawn_id = f"c5-stage-{uuid.uuid4().hex[:8]}"
    ralph_root = Path(__file__).resolve().parents[2]

    child_script = tmp_path / "c5_stage_child.py"
    child_script.write_text(
        f"""import sys, time
from pathlib import Path
sys.path.insert(0, {str(ralph_root)!r})
from ralph.mcp.server._start_stage_tracker import record
record(Path({str(tmp_path)!r}), "ready_to_serve")
while True:
    time.sleep(0.1)
""",
        encoding="utf-8",
    )

    proc = managed_spawner(
        [sys.executable, str(child_script)],
        env={MCP_STAGE_ID_ENV: spawn_id},
        label="c5-diag-child",
    )

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if read_last_stage(tmp_path, spawn_id=spawn_id) is not None:
            break
        time.sleep(0.02)

    p = psutil.Process(proc.pid)
    assert p.status() in (psutil.STATUS_RUNNING, psutil.STATUS_SLEEPING)

    proc.kill()
    proc.wait(timeout=2.0)

    last_stage = read_last_stage(tmp_path, spawn_id=spawn_id)
    assert last_stage == "ready_to_serve"

    err = McpServerError(
        "abnormal termination",
        restart_count=1,
        handover_step="spawn_candidate",
        psutil_status="killed",
    )
    assert err.handover_step == "spawn_candidate"
    assert err.psutil_status == "killed"

    suffix = lifecycle._startup_diagnostic_suffix(tmp_path, 0)
    assert "child wrote no log output" in suffix


_E22_FAIL_MODES: tuple[str, ...] = (
    "pre_socket_exit",
    "import_fail",
    "session_fail",
    "config_fail",
    "catalog_fail",
    "registry_fail",
    "init_fail",
    "tools_drift",
)


def _spawn_e22_stage_failure_child(
    tmp_path: Path,
    fail_mode: str,
    *,
    fd: int,
    tools_json: str,
) -> tuple[list[str], dict[str, str], tuple[int, ...]]:
    if fail_mode == "pre_socket_exit":
        return [sys.executable, "-c", "import sys; sys.exit(42)"], {}, ()

    common_preamble = (
        "import json, os, socket, sys\n"
        "fd = int(sys.argv[1])\n"
        "tools = json.loads(sys.argv[2])\n"
        "sock = socket.socket(fileno=fd)\n"
        "sock.settimeout(5.0)\n"
    )

    body_for: dict[str, tuple[str, bool]] = {
        "import_fail": (
            "import sys\n"
            "try:\n"
            "    import ralph__nonexistent_module_e22_xyz_12345  # stage=import_fail\n"
            "except ImportError as exc:\n"
            "    sys.stderr.write(f'import_fail stage: {exc}\\n')\n"
            "    sys.exit(43)\n",
            False,
        ),
        "catalog_fail": (
            "import sys\n"
            "sys.stderr.write('catalog_fail stage: synthetic tool catalog load failed\\n')\n"
            "sys.exit(46)\n",
            True,
        ),
        "registry_fail": (
            "import sys\n"
            "sys.stderr.write('registry_fail stage: synthetic registry build failed\\n')\n"
            "sys.exit(47)\n",
            True,
        ),
        "init_fail": (
            "import sys\nsock.close()\n",
            True,
        ),
    }
    if fail_mode in body_for:
        body, needs_fd = body_for[fail_mode]
        if needs_fd:
            argv = [sys.executable, "-c", common_preamble + body, str(fd), tools_json]
        else:
            argv = [sys.executable, "-c", body]
        return argv, {}, (fd,) if needs_fd else ()

    if fail_mode == "session_fail":
        session_path = tmp_path / "session_to_delete.json"
        session_path.write_text("{}", encoding="utf-8")
        session_path.unlink()
        body = (
            "import sys\n"
            f"sp = {str(session_path)!r}\n"
            "try:\n"
            "    with open(sp, 'r', encoding='utf-8') as fh:\n"
            "        fh.read()\n"
            "except FileNotFoundError as exc:\n"
            "    sys.stderr.write(f'session_fail stage: {exc}\\n')\n"
            "    sys.exit(44)\n"
        )
        return [sys.executable, "-c", body], {}, ()

    if fail_mode == "config_fail":
        missing_toml = tmp_path / "mcp_toml_deleted.toml"
        body = (
            "import sys\n"
            "try:\n"
            f"    with open({str(missing_toml)!r}, 'r', encoding='utf-8') as fh:\n"
            "        _ = fh.read()\n"
            "except FileNotFoundError as exc:\n"
            "    sys.stderr.write(f'config_fail stage: {exc}\\n')\n"
            "    sys.exit(45)\n"
        )
        return [sys.executable, "-c", body], {}, ()

    if fail_mode == "tools_drift":
        responder_script = _write_responder_script(tmp_path)
        env = {"RALPH_MCP_STAGE_ID": f"c5-cand-{fail_mode}"}
        return (
            [sys.executable, str(responder_script), str(fd), tools_json],
            env,
            (fd,),
        )

    msg = f"unknown fail_mode: {fail_mode}"
    raise AssertionError(msg)


@pytest.mark.parametrize("fail_mode", _E22_FAIL_MODES)
def test_c5_e22_candidate_stage_failures_parametrized(
    managed_spawner: Callable[..., ManagedProcess],
    tmp_path: Path,
    fail_mode: str,
) -> None:
    preexisting_pids = _descendant_pids()

    old_proc = managed_spawner(
        ["sleep", "300"],
        label="c5-e22-old-child",
    )
    old_inner = _make_real_standalone(old_proc, tmp_path, stage_id="c5-e22-old")

    tools_base = [{"name": "read_file", "description": "read", "inputSchema": {}}]
    tools_drift = [{"name": "drifted_e22", "description": "drifted", "inputSchema": {}}]
    cand_procs: list[ManagedProcess] = []
    cand_stage_for = f"c5-cand-{fail_mode}"
    parent_sock: socket.socket | None = None
    needs_handover = fail_mode in {"catalog_fail", "registry_fail", "init_fail", "tools_drift"}

    def make_candidate() -> StandaloneMcpProcess:
        nonlocal parent_sock
        psock: socket.socket | None
        csock: socket.socket | None
        fd: int
        if needs_handover:
            psock, csock = create_handover_socketpair()
            fd = csock.fileno()
        else:
            psock, csock = None, None
            fd = -1
        try:
            tools_arg = (
                json.dumps(tools_drift) if fail_mode == "tools_drift" else json.dumps(tools_base)
            )
            argv, env, pass_fds = _spawn_e22_stage_failure_child(
                tmp_path, fail_mode, fd=fd, tools_json=tools_arg
            )
            label = f"c5-e22-{fail_mode}-cand"
            cand_proc = managed_spawner(argv, env=env, pass_fds=pass_fds, label=label)
        finally:
            if csock is not None:
                with contextlib.suppress(Exception):
                    csock.close()
        if not needs_handover:
            with contextlib.suppress(Exception):
                cand_proc.wait(timeout=3.0)
        cand_procs.append(cand_proc)
        parent_sock = psock
        return _make_real_standalone(
            cand_proc,
            tmp_path,
            stage_id=cand_stage_for,
            handover_parent_sock=psock,
        )

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=make_candidate,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id=f"test-c5-e22-{fail_mode}-{uuid.uuid4().hex[:8]}",
        sleep_fn=lambda s: None,
        workspace_root=tmp_path,
    )
    bridge._expected_tool_hash = compute_tool_set_hash(tools_base)

    assert bridge.restart_count == 0
    try:
        with bridge._lock:
            result = bridge._perform_restart(process_exited=False, probe_duration_s=1.0)
    finally:
        if parent_sock is not None:
            with contextlib.suppress(Exception):
                parent_sock.close()

    assert result is False, f"fail_mode={fail_mode}: expected non-terminal result, got promotion"
    assert bridge.restart_count == 1, (
        f"fail_mode={fail_mode}: restart attempt was not counted (got {bridge.restart_count})"
    )

    assert bridge.process.pid == old_proc.pid, (
        f"fail_mode={fail_mode}: bridge swapped to a candidate after a stage failure"
    )
    assert old_proc.poll() is None, (
        f"fail_mode={fail_mode}: old child was killed by the bridge after a stage failure"
    )

    assert cand_procs, f"fail_mode={fail_mode}: no candidate was spawned"
    for cp in cand_procs:
        with contextlib.suppress(Exception):
            cp.wait(timeout=3.0)
        poll_after = cp.poll()
        assert poll_after is not None, (
            f"fail_mode={fail_mode}: candidate PID={cp.pid} still alive after stage failure"
        )

    descendants = _descendant_pids() - preexisting_pids - {old_proc.pid}
    assert descendants == set(), (
        f"fail_mode={fail_mode}: unrelated descendant PIDs leaked: {sorted(descendants)}"
    )


def test_cited_impossibility_pins(
    managed_spawner: Callable[..., ManagedProcess],
) -> None:
    proc = managed_spawner(
        ["true"],
        label="zombie-pin-child",
    )
    proc.wait(timeout=2.0)

    poll_result = proc.poll()
    assert poll_result is not None
    assert poll_result == 0

    with pytest.raises(ProcessLookupError):
        os.kill(proc.pid, 0)
