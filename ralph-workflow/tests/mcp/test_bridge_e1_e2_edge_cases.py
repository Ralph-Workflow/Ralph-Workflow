from __future__ import annotations

import contextlib
import http.client
import json
import multiprocessing
import pathlib
import sys
import tempfile
import threading
import time
import uuid

import pytest

from ralph.mcp.protocol._startup_timeouts import (
    _CONFIG_LOAD_CUMULATIVE_S,
    _REGISTRY_BUILT_CUMULATIVE_S,
    _WHOLE_STARTUP_BUDGET_S,
    mcp_preflight_timeout_from_env,
)
from ralph.mcp.server import lifecycle
from ralph.mcp.server._handover_protocol import (
    _HANDOVER_ACK_TIMEOUT_S,
    _HANDOVER_DRAIN_S,
    compute_tool_set_hash,
    create_handover_socketpair,
    request_old_child_pause,
    verify_candidate_readiness,
    wait_for_old_child_pause_ack,
)
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._snapshots import read_snapshot, snapshot_dir, write_snapshot
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from ralph.mcp.server._wire_ledger import append_wire_record, verify_chain
from ralph.process.manager import ProcessTerminationIssuer, SpawnOptions, get_process_manager

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

    def terminate(self, grace_period_s: float = 5.0, **_kwargs: object) -> None:
        self.terminated = True
        self._poll_result = 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self, **_kwargs: object) -> None:
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


def _ledger_append_worker(
    workspace_root: pathlib.Path,
    secret: str,
    run_id: str,
    worker_id: str,
    count: int,
) -> None:
    for i in range(count):
        append_wire_record(
            workspace_root,
            method="tools/call",
            tool_name=f"tool_{worker_id}_{i}",
            params={"worker": worker_id, "index": i},
            run_id=run_id,
            secret=secret,
        )


def test_e11_private_readiness_rejects_spawn_id_mismatch() -> None:
    parent_sock, child_sock = create_handover_socketpair()
    try:
        ok, stage, _err = verify_candidate_readiness(
            parent_sock,
            expected_spawn_id="spawn-123",
            expected_pid=1234,
            expected_session_id="session-xyz",
            expected_broker_fingerprint=None,
            timeout=0.1,
        )
        assert ok is False
        assert stage in ("private_preflight_timeout", "private_preflight_invalid_response")
    finally:
        parent_sock.close()
        child_sock.close()

    parent_sock2, child_sock2 = create_handover_socketpair()
    try:

        def _responder() -> None:
            try:
                line = child_sock2.recv(4096)
                if line:
                    parsed: object = json.loads(line.decode("utf-8"))
                    req_id: object = 1
                    if isinstance(parsed, dict):
                        raw_id = parsed.get("id", 1)
                        if isinstance(raw_id, (int, str)):
                            req_id = raw_id
                    resp: dict[str, object] = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "pid": 1234,
                            "spawn_id": "wrong-spawn-999",
                            "session_id": "session-xyz",
                            "broker_secret_fingerprint": None,
                        },
                    }
                    child_sock2.sendall(json.dumps(resp).encode("utf-8") + b"\n")
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                pass

        t = threading.Thread(target=_responder, daemon=True)
        t.start()
        ok2, stage2, err2 = verify_candidate_readiness(
            parent_sock2,
            expected_spawn_id="spawn-123",
            expected_pid=1234,
            expected_session_id="session-xyz",
            expected_broker_fingerprint=None,
            timeout=1.0,
        )
        t.join(timeout=1.0)
        assert ok2 is False
        assert stage2 == "spawn_id_mismatch"
        assert "wrong-spawn-999" in str(err2)
    finally:
        parent_sock2.close()
        child_sock2.close()


def test_e12_pause_ack_timeout_triggers_escalation(tmp_path: pathlib.Path) -> None:
    pause_file = tmp_path / "accept.pause"
    request_old_child_pause(pause_file)
    assert pause_file.exists()
    assert pause_file.read_text(encoding="utf-8") == "PAUSE\n"

    start = time.monotonic()
    ack = wait_for_old_child_pause_ack(pause_file, timeout=0.1)
    elapsed = time.monotonic() - start
    assert ack is False
    assert elapsed >= 0.09


def test_e12_pause_ack_received_promptly(tmp_path: pathlib.Path) -> None:
    pause_file = tmp_path / "accept.pause"
    request_old_child_pause(pause_file)
    ack_file = tmp_path / "accept.pause.ack"
    ack_file.write_text("ACK\n", encoding="utf-8")
    assert wait_for_old_child_pause_ack(pause_file, timeout=1.0) is True


def test_e13_drain_deadline_is_bounded() -> None:
    assert _HANDOVER_DRAIN_S > 0
    assert _HANDOVER_DRAIN_S <= 5.0
    assert _HANDOVER_ACK_TIMEOUT_S > 0
    assert _HANDOVER_ACK_TIMEOUT_S <= 2.0


_WEDGED_CHILD_HOLD_S = 30.0

_DRAIN_REAP_BOUND_S = _HANDOVER_DRAIN_S + 2.0


def _write_wedged_child_script(tmp_path: pathlib.Path) -> tuple[pathlib.Path, ...]:
    port_file = tmp_path / "e13_wedged_port.txt"
    serving_file = tmp_path / "e13_wedged_serving.txt"
    script = tmp_path / "e13_wedged_child.py"
    script.write_text(
        "\n".join(
            [
                "import time",
                "from http.server import BaseHTTPRequestHandler, HTTPServer",
                "from pathlib import Path",
                f"port_file = Path({str(port_file)!r})",
                f"serving_file = Path({str(serving_file)!r})",
                "",
                "class _WedgeHandler(BaseHTTPRequestHandler):",
                "    def do_POST(self) -> None:",
                "        length = int(self.headers.get('Content-Length', '0'))",
                "        if length:",
                "            self.rfile.read(length)",
                "        # ACCEPTED: record the wedge, then never answer in time.",
                "        serving_file.write_text('wedged', encoding='utf-8')",
                f"        time.sleep({_WEDGED_CHILD_HOLD_S})",
                "        body = b'{}'",
                "        self.send_response(200)",
                "        self.send_header('Content-Length', str(len(body)))",
                "        self.end_headers()",
                "        self.wfile.write(body)",
                "",
                "    def log_message(self, format, *args):",
                "        return",
                "",
                "server = HTTPServer(('127.0.0.1', 0), _WedgeHandler)",
                "port_file.write_text(str(server.server_address[1]), encoding='utf-8')",
                "server.serve_forever()",
            ]
        ),
        encoding="utf-8",
    )
    return script, port_file, serving_file


def _issue_wedged_inflight_request(
    port: int, errors: list[Exception], done: threading.Event
) -> None:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=_WEDGED_CHILD_HOLD_S + 10.0)
    try:
        conn.request(
            "POST",
            "/mcp",
            body=b'{"jsonrpc":"2.0","id":1,"method":"tools/call"}',
            headers={"Content-Type": "application/json"},
        )
        _ = conn.getresponse().read()
    except Exception as exc:
        errors.append(exc)
    finally:
        with contextlib.suppress(Exception):
            conn.close()
        done.set()


def _wait_for_path(path: pathlib.Path, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size > 0:
            return True
        time.sleep(0.02)
    return path.exists() and path.stat().st_size > 0


@pytest.mark.timeout_seconds(10)
def test_e13_hung_old_child_reaped_at_drain_deadline(tmp_path: pathlib.Path) -> None:
    script, port_file, serving_file = _write_wedged_child_script(tmp_path)
    child = get_process_manager().spawn(
        [sys.executable, str(script)],
        SpawnOptions(cwd=str(tmp_path), label="e13-wedged-old-child"),
    )

    try:
        assert _wait_for_path(port_file), "wedged child never reported its bound port"
        port = int(port_file.read_text(encoding="utf-8").strip())

        inflight_errors: list[Exception] = []
        inflight_done = threading.Event()
        worker = threading.Thread(
            target=_issue_wedged_inflight_request,
            args=(port, inflight_errors, inflight_done),
            daemon=True,
        )
        worker.start()

        assert _wait_for_path(serving_file), "child never accepted the in-flight request"
        assert not inflight_done.is_set(), (
            "accepted request on the wedged child completed; it must never complete"
        )
        assert inflight_errors == []

        session_file = tmp_path / "session.json"
        session_file.write_text("{}", encoding="utf-8")
        old_inner = StandaloneMcpProcess(
            endpoint=f"http://127.0.0.1:{port}/mcp",
            process=child,
            session_file=session_file,
            stage_id="stage-e13-wedged",
        )
        bridge = lifecycle.RestartAwareMcpBridge(
            old_inner,
            restart_fn=lambda: old_inner,
            restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
            run_id="run-e13-wedged",
            workspace_root=tmp_path,
        )
        assert bridge._sleep_fn is None

        started = time.monotonic()
        bridge._drain_old(old_inner)
        assert old_inner in bridge._draining_inners, "wedged child was not registered as draining"

        deadline = started + _DRAIN_REAP_BOUND_S
        while time.monotonic() < deadline and child.poll() is None:
            time.sleep(0.03)
        elapsed = time.monotonic() - started

        assert child.poll() is not None, (
            f"wedged old child was not reaped within {_DRAIN_REAP_BOUND_S}s "
            f"(drain deadline {_HANDOVER_DRAIN_S}s was not enforced)"
        )
        assert elapsed >= _HANDOVER_DRAIN_S * 0.9, (
            f"child was reaped after {elapsed:.3f}s, before the "
            f"{_HANDOVER_DRAIN_S}s drain deadline elapsed"
        )
        assert elapsed < _WEDGED_CHILD_HOLD_S, (
            f"retirement waited {elapsed:.3f}s for a request the wedged child "
            f"would hold for {_WEDGED_CHILD_HOLD_S}s"
        )
        assert old_inner not in bridge._draining_inners

        assert inflight_done.wait(timeout=2.0), "in-flight request never surfaced an outcome"
        assert len(inflight_errors) == 1, (
            f"in-flight request on the reaped child must surface exactly one typed error; "
            f"got {inflight_errors!r}"
        )
        assert isinstance(inflight_errors[0], OSError), (
            f"in-flight severed request must surface a typed OSError-family error; "
            f"got {type(inflight_errors[0]).__name__}: {inflight_errors[0]!r}"
        )
        worker.join(timeout=2.0)
    finally:
        with contextlib.suppress(Exception):
            child.kill(issuer=ProcessTerminationIssuer.OPERATOR)
        with contextlib.suppress(Exception):
            child.wait(timeout=2.0)


def test_e14_inflight_non_idempotent_tool_calls_no_duplicate_dispatch() -> None:
    old_inner = _make_inner(poll_result=None, pid=1001)
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
    assert old_inner.process.pid == 1001


def test_e15_inflight_calls_handled_cleanly_during_drain() -> None:
    drain_completed = threading.Event()

    def slow_drain_sleep(seconds: float) -> None:
        drain_completed.wait(timeout=2.0)

    old_inner = _make_inner(poll_result=None, pid=1001)
    candidate = _make_inner(poll_result=None, pid=2002)

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: candidate,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
        sleep_fn=slow_drain_sleep,
    )

    bridge._perform_restart(process_exited=False, probe_duration_s=1.0)
    assert bridge._inner is candidate
    assert old_inner in bridge._draining_inners
    assert old_inner.process.poll() is None

    (drain_thread,) = tuple(bridge._drain_threads)
    drain_completed.set()
    drain_thread.join(timeout=5.0)
    assert not drain_thread.is_alive(), "drain worker did not finish after drain completed"
    assert old_inner.process.poll() is not None


def test_e16_two_child_ledger_interleave(tmp_path: pathlib.Path) -> None:
    secret = "test-secret-12345"
    run_id = "test-run"
    ctx = multiprocessing.get_context("fork")

    p1 = ctx.Process(
        target=_ledger_append_worker,
        args=(tmp_path, secret, run_id, "child1", 5),
    )
    p2 = ctx.Process(
        target=_ledger_append_worker,
        args=(tmp_path, secret, run_id, "child2", 5),
    )
    p1.start()
    p2.start()
    p1.join(timeout=5.0)
    p2.join(timeout=5.0)

    assert p1.exitcode == 0
    assert p2.exitcode == 0
    assert verify_chain(tmp_path, secret) is True


def test_e17_in_memory_state_handover_via_snapshot(tmp_path: pathlib.Path) -> None:
    snap = snapshot_dir(tmp_path, "run-e17")
    write_snapshot(
        snap,
        session_id="session-e17",
        run_id="run-e17",
        broker_secret_fingerprint="sha256-fingerprint",
    )
    data = read_snapshot(snap)
    assert data["session_id"] == "session-e17"
    assert data["run_id"] == "run-e17"
    assert data["broker_secret_fingerprint"] == "sha256-fingerprint"


def test_e18_session_id_persisted_across_respawn(tmp_path: pathlib.Path) -> None:
    snap_dir = snapshot_dir(tmp_path, "run-1")
    write_snapshot(
        snap_dir,
        session_id="stable-session-id",
        run_id="run-1",
        broker_secret_fingerprint="fp123",
    )
    data1 = read_snapshot(snap_dir)
    data2 = read_snapshot(snap_dir)
    assert data1["session_id"] == "stable-session-id"
    assert data2["session_id"] == "stable-session-id"


def test_e19_tool_set_drift_detection() -> None:
    tools_a: list[dict[str, object]] = [
        {"name": "tool1", "description": "desc1", "inputSchema": {}},
        {"name": "tool2", "description": "desc2", "inputSchema": {}},
    ]
    tools_b: list[dict[str, object]] = [
        {"name": "tool1", "description": "desc1", "inputSchema": {}},
        {"name": "tool2", "description": "desc2_modified", "inputSchema": {}},
    ]
    hash_a = compute_tool_set_hash(tools_a)
    hash_b = compute_tool_set_hash(tools_b)
    assert hash_a != hash_b
    hash_a_reversed = compute_tool_set_hash(list(reversed(tools_a)))
    assert hash_a == hash_a_reversed


def test_e21_candidate_death_after_promotion_counted() -> None:
    old_inner = _make_inner(poll_result=None, pid=1001)
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

    cast_proc = candidate.process
    assert isinstance(cast_proc, _FakeProcess)
    cast_proc._poll_result = 137

    replacement = _make_inner(poll_result=None, pid=3003)
    bridge._restart_fn = lambda: replacement
    restarted = bridge.check_health_and_restart_if_needed()
    assert restarted is True
    assert bridge.restart_count == 2
    assert bridge.process.pid == 3003


def test_e22_candidate_spawn_failure_keeps_old_child() -> None:
    old_inner = _make_inner(poll_result=None, pid=1001)

    def failing_spawn() -> StandaloneMcpProcess:
        raise OSError("EMFILE: too many open files")

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=failing_spawn,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
    )

    assert bridge._perform_restart(process_exited=False, probe_duration_s=1.0) is False
    assert bridge.process.pid == 1001
    assert old_inner.process.poll() is None


def test_e23_startup_deadline_bounded() -> None:
    assert _CONFIG_LOAD_CUMULATIVE_S > 0
    assert _REGISTRY_BUILT_CUMULATIVE_S > _CONFIG_LOAD_CUMULATIVE_S
    assert _WHOLE_STARTUP_BUDGET_S > _REGISTRY_BUILT_CUMULATIVE_S
    preflight_timeout = mcp_preflight_timeout_from_env().total_seconds()
    assert preflight_timeout > _WHOLE_STARTUP_BUDGET_S


def test_e24_stage_id_fresh_per_spawn() -> None:
    id1 = str(uuid.uuid4())
    id2 = str(uuid.uuid4())
    assert id1 != id2


def test_e25_snapshot_survives_tmp_cleanup(tmp_path: pathlib.Path) -> None:
    snap_dir = snapshot_dir(tmp_path, "run-e25")
    write_snapshot(
        snap_dir,
        session_id="sess-e25",
        run_id="run-e25",
        broker_secret_fingerprint=None,
    )
    assert (snap_dir / "session.json").exists()

    workspace_tmp = tmp_path / ".agent" / "tmp"
    workspace_tmp.mkdir(parents=True, exist_ok=True)
    (workspace_tmp / "junk.txt").write_text("junk", encoding="utf-8")

    import shutil

    shutil.rmtree(workspace_tmp)

    assert (snap_dir / "session.json").exists()
    assert read_snapshot(snap_dir)["session_id"] == "sess-e25"


def test_e26_broken_workspace_ralph_does_not_affect_candidate(tmp_path: pathlib.Path) -> None:
    broken_pkg = tmp_path / "ralph"
    broken_pkg.mkdir(parents=True, exist_ok=True)
    (broken_pkg / "__init__.py").write_text(
        "raise SyntaxError('broken workspace')", encoding="utf-8"
    )

    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    env = lifecycle._subprocess_env(session_file)

    pythonpath = env.get("PYTHONPATH", "")
    entries = pythonpath.split(":")
    assert entries[0] == str(lifecycle._PACKAGE_ROOT)


def test_e27_bridge_lock_serializes_restart() -> None:
    inner = _make_inner(poll_result=None, pid=1001)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_inner(poll_result=None, pid=2002),
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
        sleep_fn=lambda s: None,
    )

    assert bridge._lock.acquire(timeout=1.0) is True
    bridge._lock.release()
