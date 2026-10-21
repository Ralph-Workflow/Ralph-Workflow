"""WU-Bridge: make-before-break MCP restart state machine tests with real child processes.

Pins C1-C2 make-before-break behavior with real child processes:
- Live old child continues serving tools/list while candidate is spawned.
- Unverified candidate (exited/crashed or private-preflight identity mismatch) is discarded without promoting.
- Successful candidate is promoted to _inner while old child is drained in background.
- Failed restart attempt keeps old child serving and does not break the bridge.
- Budget exhaustion occurs only after max_restarts failed attempts, with old child continuously serving.
- Full process lifecycle verification: all child processes are reaped with zero leaks.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import sys
import time
import uuid
from pathlib import Path

import psutil
import pytest

from ralph.mcp.server import lifecycle
from ralph.mcp.server._handover_protocol import create_handover_socketpair
from ralph.mcp.server._http_tools_list import _http_tools_list_names
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from ralph.process.manager import ManagedProcess, SpawnOptions, get_process_manager

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.timeout_seconds(15),
]


def _pid_alive(pid: int) -> bool:
    """Check if process with given PID is still alive."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return False
    return True


def _assert_child_reaped(proc: ManagedProcess) -> None:
    """Assert child process is reaped and not running."""
    assert proc.poll() is not None, f"Child PID {proc.pid} still running (poll is None)"
    assert not _pid_alive(proc.pid), f"Child PID {proc.pid} leaked (still alive)"


def _assert_no_child_leaks(tracked_pids: set[int]) -> None:
    """Assert no tracked child processes remain alive or un-reaped."""
    own_pid = os.getpid()
    descendants = {d.pid for d in psutil.Process(own_pid).children(recursive=True)}
    leaked = descendants.intersection(tracked_pids)
    assert leaked == set(), f"Child processes leaked: {leaked}"
    for pid in tracked_pids:
        assert not _pid_alive(pid), f"PID {pid} still alive"


def _write_responder_script(tmp_path: Path) -> Path:
    """Create a real child responder script serving JSON-RPC tools/list and handover socket."""
    script_path = tmp_path / "bridge_test_child.py"
    if not script_path.exists():
        script = """import argparse
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--port-file", type=Path, required=True)
parser.add_argument("--tools", default='[{"name": "read_file", "description": "read", "inputSchema": {}}]')
parser.add_argument("--handover-sock-fd", type=int, default=None)
parser.add_argument("--mismatch-pid", action="store_true")
parser.add_argument("--exit-immediately", type=int, default=None)
args = parser.parse_args()

if args.exit_immediately is not None:
    sys.exit(args.exit_immediately)

tools = json.loads(args.tools)

class _McpHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        req = json.loads(body.decode("utf-8")) if body else {}
        method = req.get("method")
        msg_id = req.get("id", 1)
        if method == "tools/list":
            res = {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools}}
        else:
            res = {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        data = json.dumps(res).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"healthy": true}')

    def log_message(self, format: str, *args: object) -> None:
        return

httpd = HTTPServer(("127.0.0.1", 0), _McpHandler)
port = httpd.server_address[1]
args.port_file.write_text(str(port), encoding="utf-8")

if args.handover_sock_fd is not None:
    def socket_worker() -> None:
        sock = socket.socket(fileno=args.handover_sock_fd)
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
                    reported_pid = 999999 if args.mismatch_pid else os.getpid()
                    res = {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "pid": reported_pid,
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
                try:
                    sock.sendall(json.dumps(res).encode("utf-8") + b"\\n")
                except OSError:
                    break
        sock.close()

    t = threading.Thread(target=socket_worker, daemon=True)
    t.start()

httpd.serve_forever()
"""
        script_path.write_text(script, encoding="utf-8")
    return script_path


def _spawn_mcp_child(
    tmp_path: Path,
    *,
    label: str = "bridge-child",
    tools: list[dict[str, object]] | None = None,
    handover_parent_sock: socket.socket | None = None,
    child_sock_fd: int | None = None,
    mismatch_pid: bool = False,
    exit_immediately: int | None = None,
    stage_id: str | None = None,
    env: dict[str, str] | None = None,
) -> tuple[ManagedProcess, StandaloneMcpProcess]:
    """Spawn a real MCP child process and return (ManagedProcess, StandaloneMcpProcess)."""
    script = _write_responder_script(tmp_path)
    port_file = tmp_path / f"port_{uuid.uuid4().hex[:8]}.txt"
    pass_fds: tuple[int, ...] = (child_sock_fd,) if child_sock_fd is not None else ()
    default_tools: list[dict[str, object]] = [
        {"name": "read_file", "description": "read", "inputSchema": {}}
    ]
    resolved_tools: list[dict[str, object]] = tools if tools is not None else default_tools
    cmd: list[str] = [
        sys.executable,
        "-u",
        str(script),
        "--port-file",
        str(port_file),
        "--tools",
        json.dumps(resolved_tools),
    ]
    if child_sock_fd is not None:
        cmd.extend(["--handover-sock-fd", str(child_sock_fd)])
    if mismatch_pid:
        cmd.append("--mismatch-pid")
    if exit_immediately is not None:
        cmd.extend(["--exit-immediately", str(exit_immediately)])

    pm = get_process_manager()
    proc = pm.spawn(
        cmd,
        SpawnOptions(
            cwd=str(tmp_path),
            env=env,
            pass_fds=pass_fds,
            label=label,
        ),
    )

    session_file = tmp_path / f"session_{uuid.uuid4().hex[:8]}.json"
    session_file.write_text("{}", encoding="utf-8")

    if exit_immediately is not None:
        proc.wait(timeout=2.0)
        standalone = StandaloneMcpProcess(
            endpoint="http://127.0.0.1:9100/mcp",
            process=proc,
            session_file=session_file,
            stage_id=stage_id,
            handover_parent_sock=handover_parent_sock,
        )
        return proc, standalone

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not port_file.exists():
        time.sleep(0.01)
    assert port_file.exists(), f"Child process {label} did not write port file within deadline"
    port = int(port_file.read_text(encoding="utf-8").strip())
    endpoint = f"http://127.0.0.1:{port}/mcp"

    names = _http_tools_list_names(endpoint, timeout=2.0)
    assert len(names) > 0, f"Child process {label} failed to serve tools/list"

    standalone = StandaloneMcpProcess(
        endpoint=endpoint,
        process=proc,
        session_file=session_file,
        stage_id=stage_id,
        handover_parent_sock=handover_parent_sock,
    )
    return proc, standalone


def test_live_server_survives_failed_candidate(tmp_path: Path) -> None:
    """A live old child keeps serving when candidate spawn fails."""
    tracked_procs: list[ManagedProcess] = []
    try:
        old_proc, inner = _spawn_mcp_child(tmp_path, label="c2-live-old-child")
        tracked_procs.append(old_proc)
        tracked_pids = {old_proc.pid}
        endpoint = inner.endpoint

        # Invariant: Child serves tools/list before candidate attempt
        tools_before = _http_tools_list_names(endpoint, timeout=2.0)
        assert tools_before == ["read_file"]

        def failing_restart_fn() -> StandaloneMcpProcess:
            raise RuntimeError("candidate spawn failed")

        bridge = lifecycle.RestartAwareMcpBridge(
            inner,
            restart_fn=failing_restart_fn,
            restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
            run_id="test-run",
        )
        bridge.capture_tool_hash_baseline()

        result = bridge._perform_restart(process_exited=False, probe_duration_s=1.0)
        assert result is False
        assert bridge.process.pid == old_proc.pid
        assert bridge.restart_count == 1
        assert inner.process.poll() is None

        # Invariant: Continuous tools/list serving after failed candidate spawn
        tools_after = _http_tools_list_names(endpoint, timeout=2.0)
        assert tools_after == tools_before

        # Teardown assertion: bridge shutdown reaps old child without leaks
        bridge.shutdown()
        _assert_child_reaped(old_proc)
        _assert_no_child_leaks(tracked_pids)
    finally:
        for p in tracked_procs:
            if p.poll() is None:
                with contextlib.suppress(Exception):
                    p.kill()
                with contextlib.suppress(Exception):
                    p.wait(timeout=2.0)


def test_make_before_break_promotion_with_old_drain(tmp_path: Path) -> None:
    """Successful candidate is promoted and old child is drained in background."""
    tracked_procs: list[ManagedProcess] = []
    try:
        old_proc, old_inner = _spawn_mcp_child(tmp_path, label="mbb-old-child")
        tracked_procs.append(old_proc)
        tracked_pids = {old_proc.pid}
        old_endpoint = old_inner.endpoint
        assert _http_tools_list_names(old_endpoint, timeout=2.0) == ["read_file"]

        cand_procs: list[ManagedProcess] = []

        def make_candidate() -> StandaloneMcpProcess:
            parent_sock, child_sock = create_handover_socketpair()
            child_fd = child_sock.fileno()
            stage_id = "cand-stage-mbb"
            env: dict[str, str] = {"RALPH_MCP_STAGE_ID": stage_id}
            cand_proc, cand_inner = _spawn_mcp_child(
                tmp_path,
                label="mbb-cand-child",
                handover_parent_sock=parent_sock,
                child_sock_fd=child_fd,
                stage_id=stage_id,
                env=env,
            )
            child_sock.close()
            cand_procs.append(cand_proc)
            tracked_procs.append(cand_proc)
            tracked_pids.add(cand_proc.pid)
            return cand_inner

        bridge = lifecycle.RestartAwareMcpBridge(
            old_inner,
            restart_fn=make_candidate,
            restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
            run_id="test-run",
            sleep_fn=lambda s: None,
        )
        bridge.capture_tool_hash_baseline()

        result = bridge._perform_restart(process_exited=False, probe_duration_s=1.0)
        assert result is True
        assert len(cand_procs) == 1
        cand_proc = cand_procs[0]

        assert bridge.process.pid == cand_proc.pid
        assert bridge._inner.process.pid == cand_proc.pid

        # Continuous serving: candidate child now serves tools/list
        cand_endpoint = bridge.endpoint_uri()
        assert _http_tools_list_names(cand_endpoint, timeout=2.0) == ["read_file"]

        # Give background drain thread a moment to finish shutting down the old child
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and old_proc.poll() is None:
            time.sleep(0.02)
        _assert_child_reaped(old_proc)

        # Teardown assertion: bridge shutdown reaps candidate child without leaks
        bridge.shutdown()
        _assert_child_reaped(cand_proc)
        _assert_no_child_leaks(tracked_pids)
    finally:
        for p in tracked_procs:
            if p.poll() is None:
                with contextlib.suppress(Exception):
                    p.kill()
                with contextlib.suppress(Exception):
                    p.wait(timeout=2.0)


def test_preflight_over_shared_endpoint_does_not_promote_unverified_candidate(
    tmp_path: Path,
) -> None:
    """Candidate that exited before promotion is not promoted; old child is kept."""
    tracked_procs: list[ManagedProcess] = []
    try:
        old_proc, old_inner = _spawn_mcp_child(tmp_path, label="unverified-old-child")
        tracked_procs.append(old_proc)
        tracked_pids = {old_proc.pid}
        old_endpoint = old_inner.endpoint
        assert _http_tools_list_names(old_endpoint, timeout=2.0) == ["read_file"]

        # Scenario 1: Dead candidate that exited before promotion
        dead_proc, dead_candidate = _spawn_mcp_child(
            tmp_path,
            label="dead-cand",
            exit_immediately=1,
        )
        tracked_procs.append(dead_proc)
        tracked_pids.add(dead_proc.pid)
        _assert_child_reaped(dead_proc)

        bridge = lifecycle.RestartAwareMcpBridge(
            old_inner,
            restart_fn=lambda: dead_candidate,
            restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
            run_id="test-run",
        )
        bridge.capture_tool_hash_baseline()

        result = bridge._perform_restart(process_exited=False, probe_duration_s=1.0)
        assert result is False
        assert bridge.process.pid == old_proc.pid
        assert bridge._inner is old_inner
        assert old_proc.poll() is None
        # Continuous serving: old child still serves tools/list
        assert _http_tools_list_names(old_endpoint, timeout=2.0) == ["read_file"]

        # Scenario 2: Candidate with mismatched preflight identity is discarded
        mismatch_procs: list[ManagedProcess] = []

        def make_mismatched_candidate() -> StandaloneMcpProcess:
            parent_sock, child_sock = create_handover_socketpair()
            child_fd = child_sock.fileno()
            stage_id = "stage-mismatch"
            env: dict[str, str] = {"RALPH_MCP_STAGE_ID": stage_id}
            cand_proc, cand_inner = _spawn_mcp_child(
                tmp_path,
                label="mismatch-cand",
                handover_parent_sock=parent_sock,
                child_sock_fd=child_fd,
                mismatch_pid=True,
                stage_id=stage_id,
                env=env,
            )
            child_sock.close()
            mismatch_procs.append(cand_proc)
            tracked_procs.append(cand_proc)
            tracked_pids.add(cand_proc.pid)
            return cand_inner

        bridge._restart_fn = make_mismatched_candidate
        result_mismatch = bridge._perform_restart(process_exited=False, probe_duration_s=1.0)
        assert result_mismatch is False
        assert bridge.process.pid == old_proc.pid
        assert bridge._inner is old_inner
        assert old_proc.poll() is None
        assert _http_tools_list_names(old_endpoint, timeout=2.0) == ["read_file"]

        # Invariant: Mismatched candidate was reaped upon abandonment
        assert len(mismatch_procs) == 1
        mismatch_proc = mismatch_procs[0]
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and mismatch_proc.poll() is None:
            time.sleep(0.02)
        _assert_child_reaped(mismatch_proc)

        # Teardown assertion: bridge shutdown reaps old child without leaks
        bridge.shutdown()
        _assert_child_reaped(old_proc)
        _assert_no_child_leaks(tracked_pids)
    finally:
        for p in tracked_procs:
            if p.poll() is None:
                with contextlib.suppress(Exception):
                    p.kill()
                with contextlib.suppress(Exception):
                    p.wait(timeout=2.0)


def test_single_failed_attempt_does_not_set_inner_to_drained_inner(
    tmp_path: Path,
) -> None:
    """A single failed attempt does not set _inner to a shut-down inner."""
    tracked_procs: list[ManagedProcess] = []
    try:
        old_proc, old_inner = _spawn_mcp_child(tmp_path, label="failed-attempt-old")
        tracked_procs.append(old_proc)
        tracked_pids = {old_proc.pid}
        endpoint = old_inner.endpoint
        assert _http_tools_list_names(endpoint, timeout=2.0) == ["read_file"]

        def failing_restart_fn() -> StandaloneMcpProcess:
            raise OSError("fork failed")

        bridge = lifecycle.RestartAwareMcpBridge(
            old_inner,
            restart_fn=failing_restart_fn,
            restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
            run_id="test-run",
        )
        bridge.capture_tool_hash_baseline()

        result = bridge._perform_restart(process_exited=False, probe_duration_s=1.0)
        assert result is False
        assert bridge.process.pid == old_proc.pid
        assert bridge._inner is old_inner
        assert old_proc.poll() is None
        # Continuous serving: old child still serves tools/list
        assert _http_tools_list_names(endpoint, timeout=2.0) == ["read_file"]

        bridge.shutdown()
        _assert_child_reaped(old_proc)
        _assert_no_child_leaks(tracked_pids)
    finally:
        for p in tracked_procs:
            if p.poll() is None:
                with contextlib.suppress(Exception):
                    p.kill()
                with contextlib.suppress(Exception):
                    p.wait(timeout=2.0)


def test_budget_still_exhausts_after_n_failed_attempts_only(
    tmp_path: Path,
) -> None:
    """Repeated failed attempts exhaust budget only after max_restarts reached."""
    tracked_procs: list[ManagedProcess] = []
    try:
        old_proc, old_inner = _spawn_mcp_child(tmp_path, label="budget-old")
        tracked_procs.append(old_proc)
        tracked_pids = {old_proc.pid}
        endpoint = old_inner.endpoint
        assert _http_tools_list_names(endpoint, timeout=2.0) == ["read_file"]

        def failing_restart_fn() -> StandaloneMcpProcess:
            raise RuntimeError("synthetic candidate failure")

        bridge = lifecycle.RestartAwareMcpBridge(
            old_inner,
            restart_fn=failing_restart_fn,
            restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
            run_id="test-run",
        )
        bridge.capture_tool_hash_baseline()

        for i in range(1, 4):
            assert bridge._perform_restart(process_exited=False, probe_duration_s=1.0) is False
            assert bridge.restart_count == i
            assert bridge._inner is old_inner
            assert old_proc.poll() is None
            assert _http_tools_list_names(endpoint, timeout=2.0) == ["read_file"]

        with pytest.raises(McpServerError, match="restart budget exhausted"):
            bridge._perform_restart(process_exited=False, probe_duration_s=1.0)

        # Server was still alive and serving even after budget exhaustion
        assert old_proc.poll() is None
        assert _http_tools_list_names(endpoint, timeout=2.0) == ["read_file"]

        bridge.shutdown()
        _assert_child_reaped(old_proc)
        _assert_no_child_leaks(tracked_pids)
    finally:
        for p in tracked_procs:
            if p.poll() is None:
                with contextlib.suppress(Exception):
                    p.kill()
                with contextlib.suppress(Exception):
                    p.wait(timeout=2.0)
