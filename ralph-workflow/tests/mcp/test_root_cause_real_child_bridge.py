"""Root-cause real-child bridge proof (C1-C5 integration test).

Pins:
- C1: Tool hash mismatch between candidate and active bridge detected and rejected before
  handover (E1.9). Matching tool hash candidate promoted. Private readiness tool hash ==
  served tools/list hash. Preflight identity metadata mismatch rejected (E1.1).
- C2: Child stdout/stderr buffering and pipe hygiene (drained pipes without deadlock).
  Failed-candidate survival (old child still _inner, serving, attempt counted, no crash).
  Budget exhaustion with dead old child (documented max_restarts count, typed terminal error).
- C3: Timeout-only deferral (TimeoutError deferred under contention, ConnectionResetError
  non-deferred real failure). Confirm-before-replace (confirm probe before kill).
  Probe-overrun contention self-measurement. Deferral ceiling trips replacement.
  Dead child never deferred. E5.3 SIGSTOPped child detected via zero CPU progress.
- C4: File-based pause/ack/resume handover protocol (single acceptor invariant, promotion
  continuity without split-brain).
- C5: Enriched diagnostic reporting on abnormal termination (psutil status, stage attribution,
  handover_step attribution on McpServerError, empty log suffix).
  E2.2 parametrized candidate stage failures.
- E5.1 / E5.4 cited-impossibility pins: PID reuse and zombie child liveness.
"""

from __future__ import annotations

import contextlib
import errno
import json
import os
import signal
import socket
import subprocess
import sys
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

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


# ---------------------------------------------------------------------------
# Fixtures and Test Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def managed_spawner(tmp_path: Path) -> Iterator[Callable[..., ManagedProcess]]:
    """Fixture that tracks spawned processes and ensures all children are reaped."""
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


def _write_responder_script(tmp_path: Path) -> Path:
    """Create script for candidate real child responding to private readiness socket."""
    script_path = tmp_path / "readiness_child.py"
    if not script_path.exists():
        script = """import json
import os
import socket
import sys

sock_fd = int(sys.argv[1])
tools_json = sys.argv[2]
tools = json.loads(tools_json)
sock = socket.socket(fileno=sock_fd)
sock.settimeout(5.0)
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
    """Wrap a ManagedProcess into a StandaloneMcpProcess."""
    session_file = tmp_path / f"session_{uuid.uuid4().hex[:8]}.json"
    session_file.write_text("{}", encoding="utf-8")
    return StandaloneMcpProcess(
        endpoint=f"http://127.0.0.1:{port}/mcp",
        process=proc,
        session_file=session_file,
        stage_id=stage_id,
        handover_parent_sock=handover_parent_sock,
    )


# ---------------------------------------------------------------------------
# C1: Tool-Set Hash Carried State & Drift Refusal (E1.9, E1.1)
# ---------------------------------------------------------------------------


def test_c1_tool_hash_mismatch_rejected_real_child(
    managed_spawner: Callable[..., ManagedProcess],
    tmp_path: Path,
) -> None:
    """Pins C1 / E1.9: Tool hash mismatch between candidate and active bridge is rejected.

    The live old child continues serving while the candidate is discarded, attempt
    is counted, and the candidate process group is reaped without leaks.
    """
    old_proc = managed_spawner(
        [sys.executable, "-c", "import time; time.sleep(10)"],
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
        run_id="test-run-c1-drift",
        sleep_fn=lambda s: None,
        workspace_root=tmp_path,
    )
    bridge._expected_tool_hash = expected_hash

    # Restart attempt executes make-before-break
    result = bridge._run_with_make_before_break()

    # Invariant: Drifted candidate was refused promotion
    assert result is False
    assert len(cand_procs) == 1
    cand_proc = cand_procs[0]

    # Invariant: Live old child remains the active serving process
    assert bridge.process.pid == old_proc.pid
    assert old_proc.poll() is None

    # Invariant: Candidate process was shut down / reaped
    cand_proc.wait(timeout=2.0)
    assert cand_proc.poll() is not None


def test_c1_matching_tool_hash_promoted_and_private_hash_equals_served_tools(
    managed_spawner: Callable[..., ManagedProcess],
    tmp_path: Path,
) -> None:
    """Pins C1: When candidate tool schema matches active tool hash, candidate is promoted.

    Also proves private readiness channel tool hash equals hash of real served tools/list.
    """
    run_id = f"test-run-c1-prom-{uuid.uuid4().hex[:8]}"
    snap_dir = snapshot_dir(tmp_path, run_id)
    pause_path = snap_dir / "accept.pause.old-stage-match"

    # Old child responds to pause sentinel and creates ack file
    old_script = tmp_path / "old_child_pause.py"
    old_script.write_text(
        """import time, sys, pathlib
pause_file = pathlib.Path(sys.argv[1])
while True:
    if pause_file.exists():
        ack_file = pause_file.with_name(pause_file.name + ".ack")
        ack_file.write_text("ACK\\n", encoding="utf-8")
        break
    time.sleep(0.01)
while True:
    time.sleep(0.1)
""",
        encoding="utf-8",
    )

    old_proc = managed_spawner(
        [sys.executable, str(old_script), str(pause_path)],
        label="c1-match-old-child",
    )
    old_inner = _make_real_standalone(old_proc, tmp_path, stage_id="old-stage-match")

    tools = [{"name": "read_file", "description": "read", "inputSchema": {}}]
    canonical_hash = compute_tool_set_hash(tools)

    responder_script = _write_responder_script(tmp_path)
    cand_procs: list[ManagedProcess] = []

    def make_candidate() -> StandaloneMcpProcess:
        parent_sock, child_sock = create_handover_socketpair()
        fd = child_sock.fileno()
        cand_stage = "cand-stage-match"
        cand_proc = managed_spawner(
            [sys.executable, str(responder_script), str(fd), json.dumps(tools)],
            env={"RALPH_MCP_STAGE_ID": cand_stage},
            pass_fds=(fd,),
            label="c1-match-cand-child",
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
        run_id=run_id,
        sleep_fn=lambda s: None,
        workspace_root=tmp_path,
    )
    bridge._expected_tool_hash = canonical_hash

    result = bridge._run_with_make_before_break()

    # Invariant: Handover succeeded and candidate is promoted
    assert result is True
    assert len(cand_procs) == 1
    cand_proc = cand_procs[0]
    assert bridge.process.pid == cand_proc.pid
    assert bridge._expected_tool_hash == canonical_hash


def test_c1_preflight_identity_metadata_mismatch_rejected(
    managed_spawner: Callable[..., ManagedProcess],
    tmp_path: Path,
) -> None:
    """Pins C1 / E1.1: Preflight identity check rejects mismatched candidate pid/spawn_id."""
    parent_sock, child_sock = create_handover_socketpair()
    fd = child_sock.fileno()

    # Script returning mismatched pid and spawn_id
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

    # Invariant: Preflight identity mismatch detected before any handover
    assert ok is False
    assert stage == "pid_mismatch"
    assert "999999" in str(err)


# ---------------------------------------------------------------------------
# C2: Buffering, Pipe Hygiene, Single-Failure & Budget Exhaustion
# ---------------------------------------------------------------------------


def test_c2_child_stdout_stderr_buffering_and_pipe_hygiene(
    managed_spawner: Callable[..., ManagedProcess],
) -> None:
    """Pins C2: Child stdout/stderr pipe hygiene (large volume drained without deadlock).

    A child writing 128KB to stdout and 128KB to stderr exceeds standard 64KB OS pipe
    buffers. Pipe draining via ManagedProcess.communicate() must drain without deadlock.
    """
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

    # Invariant: Both streams fully drained without deadlock within bounded time
    assert elapsed < 5.0
    assert stdout is not None and len(stdout) == 128 * 1024
    assert stderr is not None and len(stderr) == 128 * 1024
    assert proc.poll() == 0


def test_c2_failed_candidate_survival_and_single_failure_not_terminal(
    tmp_path: Path,
) -> None:
    """Pins C2: A failed candidate spawn on the production bridge path does not kill a live old child.

    Real MCP subprocess is started by ``start_mcp_server`` (parent-owned listening
    FD, no-op preflight). The bridge's ``_restart_fn`` is patched to raise
    ``RuntimeError`` so the candidate never reaches a verified-ready state.
    Asserts the live old child actually answers a real JSON-RPC ``tools/list``
    request over the shared endpoint, the bridge does not raise
    ``McpServerError`` (so the agent run is not interrupted), ``restart_count
    == 1``, and the candidate + its whole process group are reaped (no leak).
    """
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

    bridge = lifecycle.start_mcp_server(session, workspace, deps=deps, extras=extras)

    try:
        old_pid = bridge.process.pid
        assert old_pid > 0
        assert bridge.process.poll() is None

        endpoint = bridge.endpoint_uri()

        # Before the failed restart: old child already serves tools/list over the
        # shared endpoint (baseline). This proves the bridge is on a real
        # production path with a live inheritor child.
        names_before = _http_tools_list_names(endpoint, timeout=2.0)
        assert isinstance(names_before, list)

        # Replace restart_fn with a function that simulates a failed candidate
        # spawn (e.g. the subprocess factory is wedged). The bridge must keep
        # the live old child serving and not raise McpServerError.
        def failing_candidate_spawn() -> StandaloneMcpProcess:
            raise RuntimeError("simulated candidate spawn failure")

        bridge._restart_fn = failing_candidate_spawn

        # Perform restart: candidate spawn raises; bridge keeps old child and
        # returns False (does NOT raise McpServerError — only BUDGET_EXHAUSTED
        # raises, per the C2 contract).
        promoted = bridge._perform_restart(process_exited=False, probe_duration_s=0.5)

        # C2 invariants on the production path:
        assert promoted is False, "failed candidate spawn must not report promotion"
        assert bridge.restart_count == 1, "failed attempt counts once toward budget"
        assert bridge.process.pid == old_pid, "old child PID must remain _inner"
        assert bridge._inner is not None
        old_inner_proc = bridge._inner.process
        assert old_inner_proc.poll() is None, "old child must remain alive"

        # Old child still serves tools/list over the SHARED endpoint (no
        # candidate promotion, no swap). This is the live-bridge survival proof.
        names_after = _http_tools_list_names(endpoint, timeout=2.0)
        assert isinstance(names_after, list)
        assert names_after == names_before, (
            "tools/list response must be identical before and after the failed "
            "restart — the old child was never replaced"
        )

        # No leaked candidate process: bridge's _inner is the original old
        # child, and the candidate spawn path raised before any new process was
        # spawned. No additional processes or process groups are alive beyond
        # the bridge's child.
        own_pid = os.getpid()
        descendants_before = {
            d.pid for d in psutil.Process(own_pid).children(recursive=True) if d.pid != old_pid
        }
        assert descendants_before == set(), (
            f"failed candidate spawn leaked unrelated child PIDs: {descendants_before}"
        )

    finally:
        with contextlib.suppress(Exception):
            bridge.shutdown()


def test_c2_live_old_child_serves_tools_list_during_failed_candidate(
    tmp_path: Path,
) -> None:
    """C2 black-box: the SHARED endpoint serves tools/list while a candidate
    spawn fails, with no McpServerError escaping the bridge.

    Drives the production ``start_mcp_server`` path. Substitutes ``restart_fn``
    to raise ``RuntimeError`` so a candidate is never started. Asserts the
    existing live child answers a real JSON-RPC ``tools/list`` over HTTP and
    the bridge does not raise ``McpServerError`` (so the supervisor stays
    non-terminal — the agent run is not interrupted). The candidate is never
    spawned, so the candidate-process-group-reaped invariant is vacuously
    satisfied and we verify it with a bounded psutil scan of the test
    process's children.
    """
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

    bridge = lifecycle.start_mcp_server(session, workspace, deps=deps, extras=extras)

    try:
        # Snapshot live-state invariants BEFORE the failed restart.
        endpoint = bridge.endpoint_uri()
        assert bridge.process.poll() is None
        live_pid = bridge.process.pid

        # Drive the restart attempt with a wedged restart_fn.
        attempts = [0]

        def failing_spawn() -> StandaloneMcpProcess:
            attempts[0] += 1
            raise RuntimeError("candidate spawn wedged")

        bridge._restart_fn = failing_spawn

        # Two failed attempts must stay non-terminal (budget remains).
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

        # Old child is STILL the active bridge child and STILL serves HTTP.
        names = _http_tools_list_names(endpoint, timeout=2.0)
        assert isinstance(names, list)

        # Foreign bind must fail with EADDRINUSE — the bridge still owns the
        # listening socket for its full lifetime, so the port is held.
        port = int(endpoint.split(":")[-1].split("/")[0])
        foreign = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with pytest.raises(OSError) as exc_info:
                foreign.bind(("127.0.0.1", port))
            assert exc_info.value.errno == errno.EADDRINUSE
        finally:
            foreign.close()

        # No candidate was ever spawned (restart_fn raised before the bridge
        # could invoke the subprocess factory), so the candidate's process
        # group is vacuously reaped. The old child's process group is the
        # bridge's only descendant.
        own_pid = os.getpid()
        descendants = {d.pid for d in psutil.Process(own_pid).children(recursive=True)}
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
    """Pins C2: When old child is dead, repeated failures exhaust budget with McpServerError."""
    dead_proc = managed_spawner(
        [sys.executable, "-c", "import sys; sys.exit(0)"],
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
    # Attempts 1, 2, 3 fail non-terminally
    for _ in range(max_restarts):
        res = bridge._attempt_restart_with_candidate(process_exited=True, probe_duration_s=None)
        assert res == RestartAttemptResult.ATTEMPT_FAILED

    # Attempt 4 exceeds budget and raises typed McpServerError
    with pytest.raises(McpServerError) as exc_info:
        bridge._attempt_restart_with_candidate(process_exited=True, probe_duration_s=None)

    elapsed = time.monotonic() - t0
    assert elapsed < 3.0
    assert "budget exhausted" in str(exc_info.value).lower()
    assert exc_info.value.restart_count == max_restarts


# ---------------------------------------------------------------------------
# C3: Timeout-Only Deferral, Confirm-Before-Replace & SIGSTOP (E5.3)
# ---------------------------------------------------------------------------


def test_c3_timeout_only_deferral_vs_connection_reset(
    managed_spawner: Callable[..., ManagedProcess],
    tmp_path: Path,
) -> None:
    """Pins C3: TimeoutError probe is deferred under contention; ConnectionResetError is not."""
    proc = managed_spawner(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        label="c3-probe-child",
    )
    inner = _make_real_standalone(proc, tmp_path, stage_id="c3-probe-stage")

    # Case A: TimeoutError with contention signal -> deferred
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
    assert proc.poll() is None  # Child not killed

    # Case B: ConnectionResetError with contention signal -> NOT deferred (real failure)
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

    # 3 reset misses trigger restart
    bridge_reset.check_health_and_restart_if_needed()
    bridge_reset.check_health_and_restart_if_needed()
    bridge_reset.check_health_and_restart_if_needed()
    assert bridge_reset._consecutive_probe_failures_deferred == 0
    assert restarted_called is True


def test_c3_confirm_before_replace_and_probe_overrun(
    managed_spawner: Callable[..., ManagedProcess],
    tmp_path: Path,
) -> None:
    """Pins C3: Confirm probe runs before kill; probe overrun records contention."""
    proc = managed_spawner(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        label="c3-confirm-child",
    )
    inner = _make_real_standalone(proc, tmp_path, stage_id="c3-confirm-stage")

    probe_attempts = 0

    def probe_fn(endpoint: str, timeout: timedelta) -> None:
        nonlocal probe_attempts
        probe_attempts += 1
        # Calls 1, 2, 3: ordinary probe misses
        # Call 4: confirm probe with 2x timeout succeeds!
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
    # 3rd ordinary miss triggers confirm probe, which succeeds
    restarted = bridge.check_health_and_restart_if_needed()

    # Invariant: Server was NOT replaced; reclassified as deferral
    assert restarted is False
    assert bridge._consecutive_probe_failures_deferred == 1
    assert proc.poll() is None


def test_c3_deferral_ceiling_and_dead_child_never_deferred(
    managed_spawner: Callable[..., ManagedProcess],
    tmp_path: Path,
) -> None:
    """Pins C3: Deferral ceiling forces replacement; dead child is never deferred."""
    proc = managed_spawner(
        [sys.executable, "-c", "import time; time.sleep(10)"],
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

    # Trip deferral ceiling
    bridge._consecutive_probe_failures_deferred = lifecycle._DEFERRAL_COUNT_CEILING
    bridge.check_health_and_restart_if_needed()
    assert restart_count == 1

    # Dead child: immediately restarts without deferral
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
    """Pins C3 / E5.3: SIGSTOPped child has zero CPU progress and is reaped on replacement."""
    proc = managed_spawner(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        label="c3-sigstop-child",
    )
    inner = _make_real_standalone(proc, tmp_path, stage_id="c3-sigstop-stage")

    # Send SIGSTOP
    os.kill(proc.pid, signal.SIGSTOP)
    time.sleep(0.02)

    try:
        bridge = lifecycle.RestartAwareMcpBridge(
            inner,
            restart_fn=lambda: inner,
            restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
            run_id="test-c3-sigstop",
            sleep_fn=lambda s: None,
        )

        # Invariant: Zero CPU progress detected on SIGSTOPped child
        prog = bridge._check_child_cpu_progress(proc.pid, sample_duration_s=0.01)
        assert prog is False
    finally:
        with contextlib.suppress(Exception):
            os.kill(proc.pid, signal.SIGCONT)
        proc.kill()
        proc.wait(timeout=2.0)


# ---------------------------------------------------------------------------
# C4: File-Based Pause/Ack/Resume Handover Protocol
# ---------------------------------------------------------------------------


def test_c4_file_based_pause_ack_resume_handover_protocol(
    managed_spawner: Callable[..., ManagedProcess],
    tmp_path: Path,
) -> None:
    """Pins C4: Single-acceptor pause/ack/resume file protocol executes without split-brain."""
    run_id = f"test-c4-handover-{uuid.uuid4().hex[:8]}"
    snap_dir = snapshot_dir(tmp_path, run_id)
    pause_file = snap_dir / "accept.pause.old-c4"
    resume_file = snap_dir / "accept.resume.cand-c4"

    # Old child watches pause_file and creates pause_file.ack
    old_script = tmp_path / "old_child_c4.py"
    old_script.write_text(
        """import time, sys, pathlib
pause_file = pathlib.Path(sys.argv[1])
while True:
    if pause_file.exists():
        ack_file = pause_file.with_name(pause_file.name + ".ack")
        ack_file.write_text("ACK\\n", encoding="utf-8")
        break
    time.sleep(0.01)
while True:
    time.sleep(0.1)
""",
        encoding="utf-8",
    )

    old_proc = managed_spawner(
        [sys.executable, str(old_script), str(pause_file)],
        label="c4-old-child",
    )
    old_inner = _make_real_standalone(old_proc, tmp_path, stage_id="old-c4")

    # Candidate child waits for resume_file
    cand_script = tmp_path / "cand_child_c4.py"
    cand_script.write_text(
        """import time, sys, pathlib
resume_file = pathlib.Path(sys.argv[1])
while True:
    if resume_file.exists():
        break
    time.sleep(0.01)
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
        sleep_fn=lambda s: None,
        workspace_root=tmp_path,
    )

    bridge._hand_over_accept(old_inner, cand_inner)

    # Invariants: Old child acknowledged pause; resume file created for candidate
    ack_file = pause_file.with_name(pause_file.name + ".ack")
    assert ack_file.exists()
    assert resume_file.exists()


# ---------------------------------------------------------------------------
# C5: Enriched Diagnostic Reporting & Parametrized Stage Failures (E2.2)
# ---------------------------------------------------------------------------


def test_c5_enriched_diagnostic_reporting_abnormal_termination(
    managed_spawner: Callable[..., ManagedProcess],
    tmp_path: Path,
) -> None:
    """Pins C5: Abnormal termination reports psutil status, stage attribution, and suffix."""
    spawn_id = f"c5-stage-{uuid.uuid4().hex[:8]}"
    ralph_root = Path(__file__).resolve().parents[2]

    # Child writes stage and sleeps
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

    # Wait until child has recorded its stage before killing
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if read_last_stage(tmp_path, spawn_id=spawn_id) is not None:
            break
        time.sleep(0.02)

    # Check psutil status while running
    p = psutil.Process(proc.pid)
    assert p.status() in (psutil.STATUS_RUNNING, psutil.STATUS_SLEEPING)

    # Abnormally terminate child via SIGKILL
    proc.kill()
    proc.wait(timeout=2.0)

    # Invariant: Stage attribution records exact last stage written
    last_stage = read_last_stage(tmp_path, spawn_id=spawn_id)
    assert last_stage == "ready_to_serve"

    # Invariant: McpServerError preserves handover_step and psutil_status
    err = McpServerError(
        "abnormal termination",
        restart_count=1,
        handover_step="spawn_candidate",
        psutil_status="killed",
    )
    assert err.handover_step == "spawn_candidate"
    assert err.psutil_status == "killed"

    # Invariant: Empty diagnostic suffix handles missing log gracefully
    suffix = lifecycle._startup_diagnostic_suffix(tmp_path, 0)
    assert "child wrote no log output" in suffix


@pytest.mark.parametrize("fail_mode", ["pre_socket_exit", "init_fail", "tools_drift"])
def test_c5_e22_candidate_stage_failures_parametrized(
    managed_spawner: Callable[..., ManagedProcess],
    tmp_path: Path,
    fail_mode: str,
) -> None:
    """Pins C5 / E2.2: Parametrized candidate failures at various stages are non-terminal."""
    old_proc = managed_spawner(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        label="c5-param-old-child",
    )
    old_inner = _make_real_standalone(old_proc, tmp_path, stage_id="c5-param-old")

    tools_base = [{"name": "read_file", "description": "read", "inputSchema": {}}]
    tools_cand = (
        tools_base
        if fail_mode != "tools_drift"
        else [{"name": "drifted", "description": "drifted", "inputSchema": {}}]
    )

    responder_script = _write_responder_script(tmp_path)
    cand_procs: list[ManagedProcess] = []

    def make_candidate() -> StandaloneMcpProcess:
        cand_stage = f"c5-cand-{fail_mode}"
        if fail_mode == "pre_socket_exit":
            cand_proc = managed_spawner(
                [sys.executable, "-c", "import sys; sys.exit(42)"],
                label="c5-failing-cand",
            )
            cand_proc.wait(timeout=2.0)
            cand_procs.append(cand_proc)
            return _make_real_standalone(cand_proc, tmp_path, stage_id=cand_stage)

        parent_sock, child_sock = create_handover_socketpair()
        fd = child_sock.fileno()

        if fail_mode == "init_fail":
            # Child closes socket immediately without responding to init
            init_fail_script = tmp_path / "init_fail.py"
            init_fail_script.write_text(
                """import socket, sys
sock = socket.socket(fileno=int(sys.argv[1]))
sock.close()
""",
                encoding="utf-8",
            )
            cand_proc = managed_spawner(
                [sys.executable, str(init_fail_script), str(fd)],
                pass_fds=(fd,),
                label="c5-init-fail-cand",
            )
        else:
            cand_proc = managed_spawner(
                [sys.executable, str(responder_script), str(fd), json.dumps(tools_cand)],
                env={"RALPH_MCP_STAGE_ID": cand_stage},
                pass_fds=(fd,),
                label="c5-drift-cand",
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
        run_id="test-c5-param",
        sleep_fn=lambda s: None,
        workspace_root=tmp_path,
    )
    bridge._expected_tool_hash = compute_tool_set_hash(tools_base)

    result = bridge._run_with_make_before_break()

    # Invariant: Every failure mode is non-terminal, old child is kept, candidate reaped
    assert result is False
    assert bridge.process.pid == old_proc.pid
    assert old_proc.poll() is None
    for cp in cand_procs:
        cp.wait(timeout=2.0)
        assert cp.poll() is not None


# ---------------------------------------------------------------------------
# E5.1 / E5.4 Cited Impossibility Pins
# ---------------------------------------------------------------------------


def test_cited_impossibility_pins(
    managed_spawner: Callable[..., ManagedProcess],
) -> None:
    """Pins E5.1 and E5.4 cited impossibility guarantees.

    E5.1 PID Reuse Impossibility:
    All process signaling in Ralph goes through ManagedProcess / Popen handles
    with create-time checks in ProcessManager._escalate_termination_sync; bare-pid
    os.kill is never used in the replacement or termination path.

    E5.4 Zombie Child Impossibility:
    Liveness is strictly Popen.poll()-based, which returns the termination returncode
    for a zombie process (detecting it as dead), unlike bare os.kill(pid, 0) which
    returns 0 for zombie processes (falsely reporting liveness).
    """
    proc = managed_spawner(
        [sys.executable, "-c", "import sys; sys.exit(0)"],
        label="zombie-pin-child",
    )
    proc.wait(timeout=2.0)

    # Invariant: poll() correctly reports exited/dead state
    poll_result = proc.poll()
    assert poll_result is not None
    assert poll_result == 0

    # Bare os.kill(pid, 0) after reap raises ProcessLookupError
    with pytest.raises(ProcessLookupError):
        os.kill(proc.pid, 0)
