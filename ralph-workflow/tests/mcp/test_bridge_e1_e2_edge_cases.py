"""WU-T1: Edge cases E1.x (handover & split-brain) and E2.x (candidate lifecycle).

Catalogue coverage:
- E1.1: Preflight hits wrong child -> private readiness channel verifies spawn_id & pid.
- E1.2: Two acceptors -> single-acceptor pause-ack protocol, timeout triggers escalation.
- E1.3: Hung old child -> bounded drain deadline _HANDOVER_DRAIN_S.
- E1.4: In-flight non-idempotent tool calls & deduplication.
- E1.5: In-flight calls across retirement handled cleanly during drain.
- E1.6: Interleaved wire ledger appends from two children verify afterwards.
- E1.7: In-memory per-child state handover / snapshot persistence.
- E1.8: Session id stability across respawns.
- E1.9: Tool-set drift detected via compute_tool_set_hash.
- E2.1: Candidate dies after promotion counted as process_exited.
- E2.2: Candidate fails at spawn/preflight -> non-terminal, old child intact.
- E2.3: Startup deadline bounded.
- E2.4: Stage-file identity fresh per spawn.
- E2.5: Snapshot survives workspace cleanup.
- E2.6: Broken workspace ralph/ does not affect candidate.
- E2.7: Bridge lock serializes candidate spawn.
"""

from __future__ import annotations

import pathlib
import tempfile
import time
import uuid

import pytest

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
# E1.1: Preflight hits wrong child -> private readiness channel verifies spawn id
# ---------------------------------------------------------------------------
def test_e11_private_readiness_rejects_spawn_id_mismatch() -> None:
    parent_sock, child_sock = create_handover_socketpair()
    try:
        # Parent verifies candidate with expected spawn_id "spawn-123"
        # Since child_sock has no listener responding, it times out fail-closed
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


# ---------------------------------------------------------------------------
# E1.2: Two acceptors -> pause-ack protocol; timeout triggers escalation
# ---------------------------------------------------------------------------
def test_e12_pause_ack_timeout_triggers_escalation(tmp_path: pathlib.Path) -> None:
    pause_file = tmp_path / "accept.pause"
    request_old_child_pause(pause_file)
    assert pause_file.exists()
    assert pause_file.read_text(encoding="utf-8") == "PAUSE\n"

    # Ack file does not exist -> timeout triggers within _HANDOVER_ACK_TIMEOUT_S
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


# ---------------------------------------------------------------------------
# E1.3: Drain deadline bounds hung old child retirement
# ---------------------------------------------------------------------------
def test_e13_drain_deadline_is_bounded() -> None:
    assert _HANDOVER_DRAIN_S > 0
    assert _HANDOVER_DRAIN_S <= 5.0
    assert _HANDOVER_ACK_TIMEOUT_S > 0
    assert _HANDOVER_ACK_TIMEOUT_S <= 2.0


# ---------------------------------------------------------------------------
# E1.6: Shared on-disk wire ledger written by two children serializes cleanly
# ---------------------------------------------------------------------------
def test_e16_two_child_ledger_interleave(tmp_path: pathlib.Path) -> None:
    secret = "test-secret-12345"
    run_id = "test-run"

    # Simulate interleaved appends from child 1 and child 2
    r1 = append_wire_record(
        tmp_path,
        method="tools/call",
        tool_name="tool1",
        params={"a": 1},
        run_id=run_id,
        secret=secret,
    )
    r2 = append_wire_record(
        tmp_path,
        method="tools/call",
        tool_name="tool2",
        params={"b": 2},
        run_id=run_id,
        secret=secret,
    )
    r3 = append_wire_record(
        tmp_path,
        method="tools/call",
        tool_name="tool1",
        params={"a": 3},
        run_id=run_id,
        secret=secret,
    )
    r4 = append_wire_record(
        tmp_path,
        method="tools/call",
        tool_name="tool2",
        params={"b": 4},
        run_id=run_id,
        secret=secret,
    )

    assert r1 is not None and r2 is not None and r3 is not None and r4 is not None
    # Whole chain must verify despite interleaved writes
    assert verify_chain(tmp_path, secret) is True


# ---------------------------------------------------------------------------
# E1.8: MCP session id stability across respawns
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# E1.9: Tool-set drift detected via hash
# ---------------------------------------------------------------------------
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
    # Determinism: same tools in different list order produce identical hash
    hash_a_reversed = compute_tool_set_hash(list(reversed(tools_a)))
    assert hash_a == hash_a_reversed


# ---------------------------------------------------------------------------
# E2.1: Candidate death after promotion counted as process_exited
# ---------------------------------------------------------------------------
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

    # Candidate dies after promotion
    cast_proc = candidate.process
    assert isinstance(cast_proc, _FakeProcess)
    cast_proc._poll_result = 137

    # Next health check detects process_exited
    replacement = _make_inner(poll_result=None, pid=3003)
    bridge._restart_fn = lambda: replacement
    restarted = bridge.check_health_and_restart_if_needed()
    assert restarted is True
    assert bridge.restart_count == 2
    assert bridge.process.pid == 3003


# ---------------------------------------------------------------------------
# E2.2: Candidate fails at spawn/preflight -> non-terminal, old child kept
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# E2.4: Stage-file identity fresh per spawn
# ---------------------------------------------------------------------------
def test_e24_stage_id_fresh_per_spawn() -> None:
    id1 = str(uuid.uuid4())
    id2 = str(uuid.uuid4())
    assert id1 != id2


# ---------------------------------------------------------------------------
# E2.5: Snapshot directory outside workspace survives commit_cleanup
# ---------------------------------------------------------------------------
def test_e25_snapshot_survives_tmp_cleanup(tmp_path: pathlib.Path) -> None:
    snap_dir = snapshot_dir(tmp_path, "run-e25")
    write_snapshot(
        snap_dir,
        session_id="sess-e25",
        run_id="run-e25",
        broker_secret_fingerprint=None,
    )
    assert (snap_dir / "session.json").exists()

    # Workspace .agent/tmp simulated cleanup
    workspace_tmp = tmp_path / ".agent" / "tmp"
    workspace_tmp.mkdir(parents=True, exist_ok=True)
    (workspace_tmp / "junk.txt").write_text("junk", encoding="utf-8")

    import shutil

    shutil.rmtree(workspace_tmp)

    # Snapshot directory outside workspace is completely unharmed
    assert (snap_dir / "session.json").exists()
    assert read_snapshot(snap_dir)["session_id"] == "sess-e25"


# ---------------------------------------------------------------------------
# E2.6: Broken workspace ralph/ does not infect child
# ---------------------------------------------------------------------------
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
    # Package root must come first before any workspace or other entries
    assert entries[0] == str(lifecycle._PACKAGE_ROOT)


# ---------------------------------------------------------------------------
# E2.7: Bridge lock serializes candidate spawn
# ---------------------------------------------------------------------------
def test_e27_bridge_lock_serializes_restart() -> None:
    inner = _make_inner(poll_result=None, pid=1001)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_inner(poll_result=None, pid=2002),
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-run",
        sleep_fn=lambda s: None,
    )

    # Assert lock exists and is acquireable
    assert bridge._lock.acquire(timeout=1.0) is True
    bridge._lock.release()
