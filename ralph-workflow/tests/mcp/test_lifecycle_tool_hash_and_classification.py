"""Tests for tool-set hash carried state, probe classification, and confirm-before-replace (C1, C3, C5).

Pins:
- Tool-set hash equality as carried state (C1 / E1.9):
  - Matching tool hash -> candidate accepted.
  - Drift -> candidate refused, old child kept when old child is alive.
  - Drift -> candidate promoted with WARNING when old child is dead.
- Probe classification (C3):
  - Timeout error -> deferrable under fresh contention.
  - Connection reset / ECONNREFUSED -> non-deferrable (real failure) even with contention.
  - Reset plus probe overrun -> non-deferrable (reset trumps overrun).
  - Malformed response / HTTP 5xx -> non-deferrable.
- Confirm-before-replace (C3):
  - Probe fails -> confirm probe succeeds -> reclassified as deferral.
  - Probe fails -> confirm probe fails -> replacement executes.
  - Deferral ceiling reached -> confirm skipped -> replacement executes.
- C5 enrichments:
  - Empty startup log -> "child wrote no log output".
  - McpServerError carries psutil_status and handover_step.

Baseline (DEC-001, the first live-child replacement had no baseline):
- ``capture_tool_hash_baseline`` seeds ``_expected_tool_hash`` from the
  SERVED ``tools/list`` of the live first child, under a bounded timeout.
- ``start_mcp_server`` performs that capture during initial startup, before
  any candidate can overlap.
- The private-channel hash is byte-equal to the shared-endpoint served hash
  for the same registry, so a baseline is a valid drift reference.
- A failed baseline capture degrades to "no baseline" with a WARNING rather
  than crashing the bridge.
- A validated candidate hash replaces the baseline, and the NEXT candidate is
  verified against it.
- Drift refusal logs the name-level TOOL DIFF at WARNING, counts a failed
  attempt, and keeps the old child serving.

Handover step (DEC-002):
- Every McpServerError raised from the make-before-break path carries
  ``handover_step`` naming the failing stage.
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import socket
import tempfile
import threading
from collections.abc import Iterator
from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import patch

import psutil
import pytest
from loguru import logger

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle
from ralph.mcp.server._handover_protocol import (
    canonical_tool_entries,
    check_candidate_readiness,
    compute_tool_set_hash,
    create_handover_socketpair,
    describe_tool_set_diff,
    start_private_readiness_listener,
)
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess

if TYPE_CHECKING:
    from ralph.mcp.server._process_like import ProcessLike

pytestmark = [pytest.mark.timeout_seconds(10)]


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
    session_file: pathlib.Path | None = None,
) -> StandaloneMcpProcess:
    if session_file is None:
        session_file = pathlib.Path(tempfile.mkdtemp()) / "session.json"
        session_file.write_text("{}", encoding="utf-8")
    return StandaloneMcpProcess(
        endpoint=endpoint,
        process=_FakeProcess(poll_result=poll_result, pid=pid),
        session_file=session_file,
    )


class _FakeRegistry:
    """Registry double exposing the ``tools`` list the private channel reads."""

    def __init__(self, tools: list[dict[str, object]]) -> None:
        self.tools = tools


class _FakeMcpServer:
    """Minimal ``_registry`` holder shaped like the live MCP server."""

    def __init__(self, tools: list[dict[str, object]]) -> None:
        self._registry = _FakeRegistry(tools)


class _FakeListeningSocket:
    """Stand-in for the parent-owned listening socket (no real bind)."""

    def __init__(self) -> None:
        self.closed = False

    def fileno(self) -> int:
        return 7

    def close(self) -> None:
        self.closed = True


@contextlib.contextmanager
def _captured_logs() -> Iterator[list[str]]:
    """Collect loguru WARNING+ records emitted inside the block."""
    records: list[str] = []
    sink_id = logger.add(lambda message: records.append(str(message)), level="WARNING")
    try:
        yield records
    finally:
        logger.remove(sink_id)


def _serve_tool_list(
    child_sock: socket.socket,
    *,
    pid: int,
    stage_id: str,
    tools: list[dict[str, object]],
    session_id: str = "",
) -> None:
    """Answer one ``initialize`` + ``tools/list`` exchange on the private channel."""
    with child_sock:
        buf = b""
        while True:
            try:
                chunk = child_sock.recv(4096)
            except (TimeoutError, OSError):
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                msg = json.loads(line.decode("utf-8"))
                method = msg.get("method")
                if method == "initialize":
                    result: dict[str, object] = {
                        "protocolVersion": "2024-11-05",
                        "serverInfo": {"name": "ralph-mcp", "version": "0.1.0"},
                        "pid": pid,
                        "spawn_id": stage_id,
                        "session_id": session_id,
                        "broker_secret_fingerprint": None,
                    }
                elif method == "tools/list":
                    result = {"tools": tools}
                else:
                    result = {}
                resp = {"jsonrpc": "2.0", "id": msg.get("id"), "result": result}
                child_sock.sendall(json.dumps(resp).encode("utf-8") + b"\n")


def _tmp_root() -> pathlib.Path:
    """Scratch directory holding a synthetic child's session file."""
    return pathlib.Path(tempfile.mkdtemp(prefix="mcp-tool-hash-"))


def _handover_candidate(
    tmp_path: pathlib.Path,
    *,
    tools: list[dict[str, object]],
    pid: int,
    stage_id: str,
    label: str,
) -> tuple[StandaloneMcpProcess, threading.Thread]:
    """Build a candidate whose private channel serves ``tools``."""
    parent_sock, child_sock = create_handover_socketpair()
    candidate = _make_inner(
        poll_result=None,
        pid=pid,
        session_file=tmp_path / f"{label}-session.json",
    )
    candidate.handover_parent_sock = parent_sock
    candidate.stage_id = stage_id
    worker = threading.Thread(
        target=_serve_tool_list,
        args=(child_sock,),
        kwargs={"pid": pid, "stage_id": stage_id, "tools": tools},
        daemon=True,
    )
    worker.start()
    return candidate, worker


def _bridge_with_old_child(
    tmp_path: pathlib.Path,
    *,
    old_pid: int = 1001,
    workspace_root: pathlib.Path | None = None,
    restart_fn: object = None,
    tool_entries_fn: object = None,
) -> lifecycle.RestartAwareMcpBridge:
    """Build a bridge whose old child is fake but lives under ``tmp_path``."""
    inner = _make_inner(
        poll_result=None,
        pid=old_pid,
        session_file=tmp_path / "old-session.json",
    )
    kwargs: dict[str, object] = {}
    if tool_entries_fn is not None:
        kwargs["tool_entries_fn"] = tool_entries_fn
    return lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=restart_fn if restart_fn is not None else _make_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
        workspace_root=workspace_root,
        sleep_fn=lambda _seconds: None,
        **kwargs,
    )


TOOLS_BASE: list[dict[str, object]] = [
    {"name": "read_file", "description": "Read a file", "inputSchema": {"type": "object"}},
    {"name": "write_file", "description": "Write a file", "inputSchema": {"type": "object"}},
]
TOOLS_DRIFTED: list[dict[str, object]] = [
    {"name": "read_file", "description": "Read a file", "inputSchema": {"type": "object"}},
    {"name": "other_tool", "description": "Other", "inputSchema": {}},
]


# ---------------------------------------------------------------------------
# C1 / E1.9: Tool-Set Carried Hash and Drift Refusal
# ---------------------------------------------------------------------------


def test_candidate_verification_retains_tool_hash_on_success() -> None:
    """Successful candidate verification retains the candidate tool hash."""
    bridge = lifecycle.RestartAwareMcpBridge(
        _make_inner(poll_result=None, pid=1001),
        restart_fn=_make_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
    )
    tools = [{"name": "read_file", "description": "Read file", "inputSchema": {}}]
    expected_hash = compute_tool_set_hash(tools)
    candidate, _worker = _handover_candidate(
        _tmp_root(), tools=tools, pid=2002, stage_id="test-stage-1", label="s1"
    )

    # Pre-set expected tool hash
    bridge._expected_tool_hash = expected_hash
    ok = bridge._verify_candidate(candidate)
    assert ok is True
    assert bridge._expected_tool_hash == expected_hash


def test_tool_set_drift_refused_when_old_child_alive() -> None:
    """When tool set drifts, candidate is refused and old child is kept."""
    bridge = lifecycle.RestartAwareMcpBridge(
        _make_inner(poll_result=None, pid=1001),
        restart_fn=_make_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
    )
    tools_old = [{"name": "read_file", "description": "Read file", "inputSchema": {}}]
    tools_new = [{"name": "other_tool", "description": "Other", "inputSchema": {}}]
    bridge._expected_tool_hash = compute_tool_set_hash(tools_old)
    bridge._expected_tool_entries = tools_old
    candidate, _worker = _handover_candidate(
        _tmp_root(), tools=tools_new, pid=2002, stage_id="test-stage-drift", label="sd"
    )

    ok = bridge._verify_candidate(candidate)
    assert ok is False  # Refused!


def test_tool_set_drift_promoted_when_old_child_dead() -> None:
    """When old child is dead, candidate with drifted tool hash is promoted with warning."""
    # Old child is dead (poll_result = 1)
    bridge = lifecycle.RestartAwareMcpBridge(
        _make_inner(poll_result=1, pid=1001),
        restart_fn=_make_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
    )
    tools_old = [{"name": "read_file", "description": "Read file", "inputSchema": {}}]
    tools_new = [{"name": "other_tool", "description": "Other", "inputSchema": {}}]
    bridge._expected_tool_hash = compute_tool_set_hash(tools_old)
    new_hash = compute_tool_set_hash(tools_new)
    candidate, _worker = _handover_candidate(
        _tmp_root(), tools=tools_new, pid=2002, stage_id="test-stage-dead", label="sdead"
    )

    ok = bridge._verify_candidate(candidate)
    assert ok is True  # Promoted under dead-child exception!
    assert bridge._expected_tool_hash == new_hash


# ---------------------------------------------------------------------------
# C3: Probe Failure Classification (Timeout vs Reset vs Malformed)
# ---------------------------------------------------------------------------


def _probe_bridge(
    probe_fn: object,
    *,
    contended: bool,
    clock_fn: object = None,
) -> lifecycle.RestartAwareMcpBridge:
    """Bridge with a scripted probe and an explicit contention signal."""
    kwargs: dict[str, object] = {}
    if clock_fn is not None:
        kwargs["clock_fn"] = clock_fn
    return lifecycle.RestartAwareMcpBridge(
        _make_inner(poll_result=None, pid=1001),
        restart_fn=_make_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
        probe_fn=probe_fn,
        contention_signal_producer=lambda: contended,
        **kwargs,
    )


def _timing_out_probe(endpoint: str, timeout: timedelta) -> None:
    raise TimeoutError("probe timed out")


def _reset_probe(endpoint: str, timeout: timedelta) -> None:
    raise ConnectionResetError("Connection reset by peer")


def _malformed_probe(endpoint: str, timeout: timedelta) -> None:
    raise ValueError("Invalid JSON-RPC response")


def test_probe_timeout_deferred_under_fresh_contention() -> None:
    """TimeoutError probe failure IS deferred when fresh contention is present."""
    bridge = _probe_bridge(_timing_out_probe, contended=True)
    with patch.object(bridge, "_perform_restart") as mock_restart:
        restarted = bridge.check_health_and_restart_if_needed()
        assert restarted is False
        assert mock_restart.call_count == 0
        assert bridge._consecutive_probe_failures_deferred == 1


def test_probe_connection_reset_not_deferred_under_contention() -> None:
    """ConnectionResetError is a real failure and must NOT be deferred even with contention."""
    bridge = _probe_bridge(_reset_probe, contended=True)
    with patch.object(bridge, "_perform_restart") as mock_restart:
        # 1st failure (1/3), 2nd failure (2/3), 3rd failure (3/3) triggers restart.
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        assert bridge._consecutive_probe_failures_deferred == 0
        assert mock_restart.call_count == 1


def test_probe_reset_plus_overrun_not_deferred() -> None:
    """Reset co-occurring with probe overrun is NOT deferred (reset trumps overrun)."""
    clock = [1000.0]

    def reset_with_delay(endpoint: str, timeout: timedelta) -> None:
        clock[0] += 15.0  # overrun past the 5 s base probe timeout
        raise ConnectionResetError("Connection reset after delay")

    bridge = _probe_bridge(reset_with_delay, contended=True, clock_fn=lambda: clock[0])
    with patch.object(bridge, "_perform_restart") as mock_restart:
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        assert bridge._consecutive_probe_failures_deferred == 0
        assert mock_restart.call_count == 1


def test_probe_malformed_response_not_deferred() -> None:
    """Malformed response error is NOT deferred even under fresh contention."""
    bridge = _probe_bridge(_malformed_probe, contended=True)
    with patch.object(bridge, "_perform_restart") as mock_restart:
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        assert bridge._consecutive_probe_failures_deferred == 0
        assert mock_restart.call_count == 1


# ---------------------------------------------------------------------------
# C3: Confirm-Before-Replace (PA-003)
# ---------------------------------------------------------------------------


def test_confirm_before_replace_defers_on_confirm_success() -> None:
    """When ordinary probe times out but confirm probe succeeds, failure is deferred."""
    call_count = 0

    def probe_fn(endpoint: str, timeout: timedelta) -> None:
        nonlocal call_count
        call_count += 1
        # Calls 1-3: ordinary probe misses (restart threshold reached on the 3rd).
        # Call 4: the confirm probe runs (2x timeout) and succeeds.
        if call_count <= 3:
            raise TimeoutError("ordinary probe timed out")

    bridge = _probe_bridge(probe_fn, contended=False)
    with patch.object(bridge, "_perform_restart") as mock_restart:
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        # Third miss triggers the confirm probe, which succeeds.
        restarted = bridge.check_health_and_restart_if_needed()
        assert restarted is False
        assert mock_restart.call_count == 0
        # Reclassified as deferral
        assert bridge._consecutive_probe_failures_deferred == 1


def test_confirm_before_replace_replaces_on_confirm_failure() -> None:
    """When ordinary probe times out AND confirm probe fails, replacement proceeds."""
    bridge = _probe_bridge(_timing_out_probe, contended=False)
    with patch.object(bridge, "_perform_restart") as mock_restart:
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        # Probes failed -> restart executed!
        assert mock_restart.call_count == 1


def test_confirm_skipped_when_deferral_ceiling_reached() -> None:
    """When deferral ceiling is reached, confirm probe is skipped and restart forced."""
    bridge = _probe_bridge(_timing_out_probe, contended=True)
    # Set deferrals to ceiling
    bridge._consecutive_probe_failures_deferred = lifecycle._DEFERRAL_COUNT_CEILING

    with patch.object(bridge, "_perform_restart") as mock_restart:
        bridge.check_health_and_restart_if_needed()
        assert mock_restart.call_count == 1


# ---------------------------------------------------------------------------
# C5: Suffix, psutil_status, handover_step
# ---------------------------------------------------------------------------


def test_startup_diagnostic_suffix_emits_child_wrote_no_log_output_when_empty(
    tmp_path: pathlib.Path,
) -> None:
    """When child log is empty, suffix emits 'child wrote no log output'."""
    suffix = lifecycle._startup_diagnostic_suffix(tmp_path, 0)
    assert "child wrote no log output" in suffix


def test_mcp_server_error_psutil_status_and_handover_step() -> None:
    """McpServerError attributes handover_step and psutil_status are preserved."""
    err = McpServerError(
        "server failed",
        restart_count=1,
        handover_step="spawn_candidate",
        psutil_status="sleeping",
        child_pid=12345,
    )
    assert err.handover_step == "spawn_candidate"
    assert err.psutil_status == "sleeping"
    err_str = str(err)
    assert "handover_step=spawn_candidate" in err_str
    assert "psutil_status=sleeping" in err_str


def test_budget_exhausted_error_carries_psutil_status_of_live_child(tmp_path: pathlib.Path) -> None:
    """The budget-exhausted McpServerError reports the live child's psutil status."""
    bridge = _bridge_with_old_child(tmp_path, old_pid=os.getpid())
    bridge._restart_count = bridge._restart_policy.max_restarts

    with pytest.raises(McpServerError) as excinfo:
        bridge._perform_restart(process_exited=False, probe_duration_s=0.25)

    err = excinfo.value
    assert err.child_pid == os.getpid()
    assert err.psutil_status == psutil.Process(os.getpid()).status()
    assert f"psutil_status={err.psutil_status}" in str(err)


def test_tool_registry_cap_error_carries_psutil_status_of_live_child(
    tmp_path: pathlib.Path,
) -> None:
    """The tool-registry cap McpServerError reports the live child's psutil status."""
    bridge = _bridge_with_old_child(tmp_path, old_pid=os.getpid())
    bridge._tool_registry_resets = lifecycle._TOOL_REGISTRY_MAX_RESETS

    with pytest.raises(McpServerError) as excinfo:
        bridge.reset_tool_registry()

    assert excinfo.value.child_pid == os.getpid()
    assert excinfo.value.psutil_status == psutil.Process(os.getpid()).status()


# ---------------------------------------------------------------------------
# DEC-001: the baseline comes from the SERVED tools/list of the live first child
# ---------------------------------------------------------------------------


def test_initial_tool_hash_timeout_is_bounded() -> None:
    """The baseline capture timeout is a bounded, import-time-checked constant."""
    assert (
        0.0 < lifecycle._INITIAL_TOOL_HASH_TIMEOUT_S < (lifecycle._INITIAL_TOOL_HASH_TIMEOUT_MAX_S)
    )


def test_capture_tool_hash_baseline_stores_served_hash(tmp_path: pathlib.Path) -> None:
    """Baseline capture hashes the served tools/list under a bounded timeout."""
    seen: list[tuple[str, float]] = []

    def _fetch(endpoint: str, timeout: float) -> list[dict[str, object]]:
        seen.append((endpoint, timeout))
        return TOOLS_BASE

    bridge = _bridge_with_old_child(tmp_path, old_pid=1001, tool_entries_fn=_fetch)
    bridge.capture_tool_hash_baseline()

    assert bridge._expected_tool_hash == compute_tool_set_hash(TOOLS_BASE)
    assert bridge._expected_tool_entries == TOOLS_BASE
    assert seen == [
        (
            "http://127.0.0.1:9100/mcp",
            lifecycle._INITIAL_TOOL_HASH_TIMEOUT_S,
        )
    ]


def test_baseline_capture_failure_degrades_to_no_baseline_with_warning(
    tmp_path: pathlib.Path,
) -> None:
    """A failed baseline capture degrades to 'no baseline' and warns, never crashes."""

    def _boom(endpoint: str, timeout: float) -> list[dict[str, object]]:
        raise TimeoutError(f"tools/list unavailable at {endpoint}")

    bridge = _bridge_with_old_child(tmp_path, old_pid=1001, tool_entries_fn=_boom)

    with _captured_logs() as records:
        bridge.capture_tool_hash_baseline()

    assert bridge._expected_tool_hash is None
    assert bridge._expected_tool_entries is None
    assert any("tool-hash baseline" in record for record in records)


def test_start_mcp_server_seeds_baseline_from_served_endpoint(tmp_path: pathlib.Path) -> None:
    """start_mcp_server captures the served baseline during initial startup."""
    fetch_calls: list[str] = []
    spawn_calls: list[list[str]] = []
    session = AgentSession(
        session_id="session-baseline",
        run_id="run-baseline",
        drain="planning",
        capabilities={"WorkspaceRead"},
    )

    def _fake_fetch(endpoint: str, timeout: float) -> list[dict[str, object]]:
        fetch_calls.append(endpoint)
        return TOOLS_BASE

    def _fake_spawn(
        command: list[str],
        cwd: pathlib.Path,
        env: dict[str, str],
        *,
        phase: str | None = None,
        pass_fds: tuple[int, ...] = (),
    ) -> ProcessLike:
        del cwd, env, phase, pass_fds
        spawn_calls.append(command)
        return _FakeProcess(poll_result=None, pid=os.getpid())

    deps = lifecycle.LifecycleDeps(
        reserve_listening_socket=lambda: ("127.0.0.1", 39411, _FakeListeningSocket()),
        create_session_file=lambda root, sess: tmp_path / "baseline-session.json",
        subprocess_env=lambda session_file: {"SESSION": str(session_file)},
        spawn_process=_fake_spawn,
        preflight=lambda endpoint, tools, timeout: None,
        preflight_timeout=lambda: timedelta(seconds=5),
    )

    with patch.object(lifecycle, "served_tool_entries", _fake_fetch):
        bridge = lifecycle.start_mcp_server(session, lifecycle.FsWorkspace(tmp_path), deps=deps)

    # Exactly one child existed at startup: the baseline was captured from the
    # served endpoint of the FIRST live child, before any candidate overlapped.
    assert len(spawn_calls) == 1
    assert fetch_calls == ["http://127.0.0.1:39411/mcp"]
    assert bridge._expected_tool_hash == compute_tool_set_hash(TOOLS_BASE)
    bridge.shutdown()


def test_private_channel_hash_equals_shared_endpoint_served_hash() -> None:
    """The private channel and the shared endpoint hash the same served tools/list.

    This is the byte-equality proof that makes a parent-side baseline a valid
    drift reference: both channels canonicalise through
    ``canonical_tool_entries`` before hashing.
    """
    served: list[dict[str, object]] = [
        {"name": "read_file", "description": "Read a file", "inputSchema": {"type": "object"}},
        {"name": "mcp__ralph__read_file", "description": "Read a file", "inputSchema": {}},
        {"name": "write_file", "description": "Write a file", "inputSchema": {}},
    ]
    parent_sock, child_sock = create_handover_socketpair()
    start_private_readiness_listener(
        child_sock,
        _FakeMcpServer(served),
        stage_id="stage-eq",
        session_id="",
        broker_fingerprint=None,
    )

    result = check_candidate_readiness(
        parent_sock,
        expected_spawn_id="stage-eq",
        expected_pid=os.getpid(),
        expected_session_id="",
        expected_broker_fingerprint=None,
        expected_tool_hash=None,
    )
    parent_sock.close()

    assert result.ok is True
    assert list(result.tools) == canonical_tool_entries(served)
    assert result.tool_hash == compute_tool_set_hash(canonical_tool_entries(served))


def test_canonical_tool_entries_collapse_non_canonical_variants() -> None:
    """Extra keys / missing fields collapse to the canonical drift-hash shape."""
    served = [
        {"name": "read_file", "description": "Read", "inputSchema": {}, "annotations": {"x": 1}},
        {"name": "write_file", "inputSchema": {"type": "object"}, "title": "ignored"},
    ]
    canonical = canonical_tool_entries(served)
    assert canonical == [
        {"name": "read_file", "description": "Read", "inputSchema": {}},
        {"name": "write_file", "description": "", "inputSchema": {"type": "object"}},
    ]
    # Idempotent: canonicalising an already-canonical list changes nothing, so a
    # baseline captured from the HTTP channel and a candidate verified over the
    # private channel hash the identical bytes.
    assert canonical_tool_entries(canonical) == canonical


# ---------------------------------------------------------------------------
# DEC-001: drift refusal keeps the old child and names the tools that differ
# ---------------------------------------------------------------------------


def test_tool_set_drift_logs_tool_diff_and_counts_failed_attempt(tmp_path: pathlib.Path) -> None:
    """Drift refuses promotion, logs the tool diff, counts the attempt, keeps the old child."""
    candidates: list[StandaloneMcpProcess] = []

    def _spawn_candidate() -> StandaloneMcpProcess:
        candidate, _worker = _handover_candidate(
            tmp_path,
            tools=TOOLS_DRIFTED,
            pid=2002,
            stage_id="cand-drift",
            label="drift",
        )
        candidates.append(candidate)
        return candidate

    bridge = _bridge_with_old_child(tmp_path, old_pid=1001, restart_fn=_spawn_candidate)
    bridge._expected_tool_hash = compute_tool_set_hash(TOOLS_BASE)
    bridge._expected_tool_entries = TOOLS_BASE
    bridge._durable_sink_path = tmp_path / "decisions.jsonl"

    with _captured_logs() as records, bridge._lock:
        replaced = bridge._perform_restart(process_exited=False, probe_duration_s=0.1)

    assert replaced is False
    # The attempt was counted against the budget...
    assert bridge.restart_count == 1
    # ...the old child is still the serving inner...
    assert bridge.process.pid == 1001
    # ...the drifted candidate was shut down, not promoted...
    assert len(candidates) == 1
    assert candidates[0].process.poll() is not None
    # ...and the refusal names the tools that differ.
    drift_logs = [record for record in records if "tool-set drift" in record.lower()]
    assert drift_logs, records
    assert any("other_tool" in record for record in drift_logs)
    assert any("write_file" in record for record in drift_logs)


def test_validated_candidate_hash_replaces_baseline_for_next_candidate(
    tmp_path: pathlib.Path,
) -> None:
    """A successful verification becomes the reference the next candidate is judged against."""
    bridge = _bridge_with_old_child(tmp_path, old_pid=1001)
    bridge._expected_tool_hash = compute_tool_set_hash(TOOLS_BASE)
    bridge._expected_tool_entries = list(TOOLS_BASE)

    matching, _w1 = _handover_candidate(
        tmp_path, tools=TOOLS_BASE, pid=2002, stage_id="cand-a", label="a"
    )
    assert bridge._verify_candidate(matching) is True
    assert bridge._expected_tool_hash == compute_tool_set_hash(TOOLS_BASE)

    drifted, _w2 = _handover_candidate(
        tmp_path, tools=TOOLS_DRIFTED, pid=2003, stage_id="cand-b", label="b"
    )
    # Refused: the retained hash (not None) is what the second candidate is compared to.
    assert bridge._verify_candidate(drifted) is False
    assert bridge._expected_tool_hash == compute_tool_set_hash(TOOLS_BASE)


def test_describe_tool_set_diff_reports_added_removed_and_changed() -> None:
    """The drift description separates added, removed, and changed tool names."""
    expected = [
        {"name": "read_file", "description": "Read", "inputSchema": {}},
        {"name": "gone", "description": "Gone", "inputSchema": {}},
        {"name": "changed", "description": "Before", "inputSchema": {}},
    ]
    candidate = [
        {"name": "read_file", "description": "Read", "inputSchema": {}},
        {"name": "added", "description": "Added", "inputSchema": {}},
        {"name": "changed", "description": "After", "inputSchema": {}},
    ]
    described = describe_tool_set_diff(expected, candidate)
    assert "added=['added']" in described
    assert "removed=['gone']" in described
    assert "changed=['changed']" in described


# ---------------------------------------------------------------------------
# DEC-002: make-before-break errors name the failing handover stage
# ---------------------------------------------------------------------------


def test_spawn_stage_failure_is_stamped_with_handover_step(tmp_path: pathlib.Path) -> None:
    """A candidate-spawn McpServerError names the spawn stage (field and log)."""
    raised: list[McpServerError] = []

    def _explode() -> StandaloneMcpProcess:
        err = McpServerError("spawn refused", restart_count=1)
        raised.append(err)
        raise err

    bridge = _bridge_with_old_child(tmp_path, old_pid=1001, restart_fn=_explode)
    with _captured_logs() as records:
        assert bridge._run_with_make_before_break() is False
    # Non-terminal under the C2 contract: the old child keeps serving...
    assert bridge.process.pid == 1001
    # ...but the error itself still names the stage that produced it.
    assert len(raised) == 1
    assert raised[0].handover_step == "spawn_candidate"
    assert any("handover_step=spawn_candidate" in record for record in records)


def test_verify_stage_failure_escapes_with_handover_step(tmp_path: pathlib.Path) -> None:
    """A verification-stage McpServerError escapes the make-before-break path stamped."""
    bridge = _bridge_with_old_child(tmp_path, old_pid=1001)

    def _verify(_candidate: StandaloneMcpProcess) -> bool:
        raise McpServerError("verification exploded", restart_count=1)

    with (
        patch.object(bridge, "_verify_candidate", _verify),
        pytest.raises(McpServerError) as excinfo,
    ):
        bridge._run_with_make_before_break()
    assert excinfo.value.handover_step == "verify_candidate"


def test_pause_ack_stage_failure_escapes_with_handover_step(tmp_path: pathlib.Path) -> None:
    """A pause/ack failure names the pause-ack stage, not a generic handover step."""

    def _explode(_pause_path: pathlib.Path) -> None:
        raise McpServerError("pause file write failed", restart_count=1)

    old_inner = _make_inner(poll_result=None, pid=os.getpid(), session_file=tmp_path / "o.json")
    candidate = _make_inner(poll_result=None, pid=2002, session_file=tmp_path / "c.json")
    candidate.stage_id = "cand-pause"
    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=_make_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="run-pause",
        workspace_root=tmp_path,
        sleep_fn=lambda _seconds: None,
    )

    with (
        patch.object(lifecycle, "request_old_child_pause", _explode),
        pytest.raises(McpServerError) as excinfo,
    ):
        bridge._hand_over_accept(old_inner, candidate)
    assert excinfo.value.handover_step == "pause_ack"


def test_resume_stage_failure_escapes_with_handover_step(tmp_path: pathlib.Path) -> None:
    """A resume-sentinel failure names the resume stage."""

    def _explode(_resume_path: pathlib.Path) -> None:
        raise McpServerError("resume file write failed", restart_count=1)

    old_inner = _make_inner(poll_result=None, pid=os.getpid(), session_file=tmp_path / "o.json")
    candidate = _make_inner(poll_result=None, pid=2002, session_file=tmp_path / "c.json")
    candidate.stage_id = "cand-resume"
    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=_make_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="run-resume",
        workspace_root=tmp_path,
        sleep_fn=lambda _seconds: None,
    )

    with (
        patch.object(lifecycle, "wait_for_old_child_pause_ack", lambda *a, **k: True),
        patch.object(lifecycle, "signal_candidate_resume", _explode),
        pytest.raises(McpServerError) as excinfo,
    ):
        bridge._hand_over_accept(old_inner, candidate)
    assert excinfo.value.handover_step == "resume_candidate"


def test_promote_stage_failure_escapes_with_handover_step(tmp_path: pathlib.Path) -> None:
    """A promotion failure names the promote stage."""

    def _explode(_candidate: StandaloneMcpProcess, _old: StandaloneMcpProcess) -> None:
        raise McpServerError("promotion failed", restart_count=1)

    bridge = _bridge_with_old_child(tmp_path, old_pid=1001)
    _candidate, _worker = _handover_candidate(
        tmp_path, tools=TOOLS_BASE, pid=2002, stage_id="cand-promote", label="promote"
    )
    bridge._expected_tool_hash = compute_tool_set_hash(TOOLS_BASE)
    bridge._expected_tool_entries = list(TOOLS_BASE)

    with (
        patch.object(bridge, "_verify_candidate", lambda _c: True),
        patch.object(bridge, "_promote_and_reap", _explode),
        pytest.raises(McpServerError) as excinfo,
    ):
        bridge._run_with_make_before_break()
    assert excinfo.value.handover_step == "promote_candidate"


def test_drain_stage_failure_is_not_overwritten_by_the_promote_stage(
    tmp_path: pathlib.Path,
) -> None:
    """The innermost stage wins: a drain failure keeps its own step."""

    def _explode(_old: StandaloneMcpProcess) -> None:
        raise McpServerError("drain failed", restart_count=1)

    bridge = _bridge_with_old_child(tmp_path, old_pid=1001)
    _candidate, _worker = _handover_candidate(
        tmp_path, tools=TOOLS_BASE, pid=2002, stage_id="cand-drain", label="drain"
    )
    bridge._expected_tool_hash = compute_tool_set_hash(TOOLS_BASE)
    bridge._expected_tool_entries = list(TOOLS_BASE)

    with (
        patch.object(bridge, "_verify_candidate", lambda _c: True),
        patch.object(bridge, "_drain_old", _explode),
        pytest.raises(McpServerError) as excinfo,
    ):
        bridge._run_with_make_before_break()
    assert excinfo.value.handover_step == "drain_old_child"
