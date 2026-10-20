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
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import threading
from datetime import timedelta
from unittest.mock import patch

import pytest

from ralph.mcp.server import lifecycle
from ralph.mcp.server._handover_protocol import (
    compute_tool_set_hash,
    create_handover_socketpair,
)
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess

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
) -> StandaloneMcpProcess:
    session_file = pathlib.Path(tempfile.mkdtemp()) / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    return StandaloneMcpProcess(
        endpoint=endpoint,
        process=_FakeProcess(poll_result=poll_result, pid=pid),
        session_file=session_file,
    )


# ---------------------------------------------------------------------------
# C1 / E1.9: Tool-Set Carried Hash and Drift Refusal
# ---------------------------------------------------------------------------


def test_candidate_verification_retains_tool_hash_on_success() -> None:
    """Successful candidate verification retains the candidate tool hash."""
    inner = _make_inner(poll_result=None, pid=1001)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=_make_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
    )

    parent_sock, child_sock = create_handover_socketpair()
    candidate = _make_inner(poll_result=None, pid=2002)
    candidate.handover_parent_sock = parent_sock
    candidate.stage_id = "test-stage-1"

    tools = [{"name": "read_file", "description": "Read file", "inputSchema": {}}]
    expected_hash = compute_tool_set_hash(tools)

    def _serve_candidate() -> None:
        try:
            with child_sock:
                buf = b""
                while True:
                    chunk = child_sock.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        msg = json.loads(line.decode("utf-8"))
                        if msg["method"] == "initialize":
                            resp = {
                                "jsonrpc": "2.0",
                                "id": msg["id"],
                                "result": {
                                    "pid": 2002,
                                    "spawn_id": "test-stage-1",
                                    "session_id": "",
                                    "broker_secret_fingerprint": None,
                                },
                            }
                        elif msg["method"] == "tools/list":
                            resp = {
                                "jsonrpc": "2.0",
                                "id": msg["id"],
                                "result": {"tools": tools},
                            }
                        else:
                            resp = {"jsonrpc": "2.0", "id": msg["id"], "result": {}}
                        child_sock.sendall(json.dumps(resp).encode("utf-8") + b"\n")
        except Exception:
            pass

    t = threading.Thread(target=_serve_candidate, daemon=True)
    t.start()

    # Pre-set expected tool hash
    bridge._expected_tool_hash = expected_hash
    ok = bridge._verify_candidate(candidate)
    assert ok is True
    assert bridge._expected_tool_hash == expected_hash


def test_tool_set_drift_refused_when_old_child_alive() -> None:
    """When tool set drifts, candidate is refused and old child is kept."""
    inner = _make_inner(poll_result=None, pid=1001)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=_make_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
    )

    parent_sock, child_sock = create_handover_socketpair()
    candidate = _make_inner(poll_result=None, pid=2002)
    candidate.handover_parent_sock = parent_sock
    candidate.stage_id = "test-stage-drift"

    tools_old = [{"name": "read_file", "description": "Read file", "inputSchema": {}}]
    tools_new = [{"name": "other_tool", "description": "Other", "inputSchema": {}}]
    bridge._expected_tool_hash = compute_tool_set_hash(tools_old)

    def _serve_candidate() -> None:
        try:
            with child_sock:
                buf = b""
                while True:
                    chunk = child_sock.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        msg = json.loads(line.decode("utf-8"))
                        if msg["method"] == "initialize":
                            resp = {
                                "jsonrpc": "2.0",
                                "id": msg["id"],
                                "result": {
                                    "pid": 2002,
                                    "spawn_id": "test-stage-drift",
                                    "session_id": "",
                                    "broker_secret_fingerprint": None,
                                },
                            }
                        elif msg["method"] == "tools/list":
                            resp = {
                                "jsonrpc": "2.0",
                                "id": msg["id"],
                                "result": {"tools": tools_new},
                            }
                        else:
                            resp = {"jsonrpc": "2.0", "id": msg["id"], "result": {}}
                        child_sock.sendall(json.dumps(resp).encode("utf-8") + b"\n")
        except Exception:
            pass

    t = threading.Thread(target=_serve_candidate, daemon=True)
    t.start()

    ok = bridge._verify_candidate(candidate)
    assert ok is False  # Refused!


def test_tool_set_drift_promoted_when_old_child_dead() -> None:
    """When old child is dead, candidate with drifted tool hash is promoted with warning."""
    # Old child is dead (poll_result = 1)
    inner = _make_inner(poll_result=1, pid=1001)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=_make_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
    )

    parent_sock, child_sock = create_handover_socketpair()
    candidate = _make_inner(poll_result=None, pid=2002)
    candidate.handover_parent_sock = parent_sock
    candidate.stage_id = "test-stage-dead"

    tools_old = [{"name": "read_file", "description": "Read file", "inputSchema": {}}]
    tools_new = [{"name": "other_tool", "description": "Other", "inputSchema": {}}]
    bridge._expected_tool_hash = compute_tool_set_hash(tools_old)
    new_hash = compute_tool_set_hash(tools_new)

    def _serve_candidate() -> None:
        try:
            with child_sock:
                buf = b""
                while True:
                    chunk = child_sock.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        msg = json.loads(line.decode("utf-8"))
                        if msg["method"] == "initialize":
                            resp = {
                                "jsonrpc": "2.0",
                                "id": msg["id"],
                                "result": {
                                    "pid": 2002,
                                    "spawn_id": "test-stage-dead",
                                    "session_id": "",
                                    "broker_secret_fingerprint": None,
                                },
                            }
                        elif msg["method"] == "tools/list":
                            resp = {
                                "jsonrpc": "2.0",
                                "id": msg["id"],
                                "result": {"tools": tools_new},
                            }
                        else:
                            resp = {"jsonrpc": "2.0", "id": msg["id"], "result": {}}
                        child_sock.sendall(json.dumps(resp).encode("utf-8") + b"\n")
        except Exception:
            pass

    t = threading.Thread(target=_serve_candidate, daemon=True)
    t.start()

    ok = bridge._verify_candidate(candidate)
    assert ok is True  # Promoted under dead-child exception!
    assert bridge._expected_tool_hash == new_hash


# ---------------------------------------------------------------------------
# C3: Probe Failure Classification (Timeout vs Reset vs Malformed)
# ---------------------------------------------------------------------------


def test_probe_timeout_deferred_under_fresh_contention() -> None:
    """TimeoutError probe failure IS deferred when fresh contention is present."""
    inner = _make_inner(poll_result=None, pid=1001)

    def timing_out_probe(endpoint: str, timeout: timedelta) -> None:
        raise TimeoutError("probe timed out")

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=_make_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
        probe_fn=timing_out_probe,
        contention_signal_producer=lambda: True,
    )

    with patch.object(bridge, "_perform_restart") as mock_restart:
        restarted = bridge.check_health_and_restart_if_needed()
        assert restarted is False
        assert mock_restart.call_count == 0
        assert bridge._consecutive_probe_failures_deferred == 1


def test_probe_connection_reset_not_deferred_under_contention() -> None:
    """ConnectionResetError is a real failure and must NOT be deferred even under fresh contention."""
    inner = _make_inner(poll_result=None, pid=1001)

    def reset_probe(endpoint: str, timeout: timedelta) -> None:
        raise ConnectionResetError("Connection reset by peer")

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=_make_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
        probe_fn=reset_probe,
        contention_signal_producer=lambda: True,  # Fresh contention present!
    )

    with patch.object(bridge, "_perform_restart") as mock_restart:
        # 1st failure (1/3)
        bridge.check_health_and_restart_if_needed()
        # 2nd failure (2/3)
        bridge.check_health_and_restart_if_needed()
        # 3rd failure (3/3) triggers restart!
        bridge.check_health_and_restart_if_needed()
        assert bridge._consecutive_probe_failures_deferred == 0
        assert mock_restart.call_count == 1


def test_probe_reset_plus_overrun_not_deferred() -> None:
    """Reset co-occurring with probe overrun is NOT deferred (reset trumps overrun)."""
    inner = _make_inner(poll_result=None, pid=1001)

    # Fake clock that advances past probe timeout to trigger overrun
    current_time = 1000.0

    def fake_clock() -> float:
        nonlocal current_time
        return current_time

    def reset_with_delay(endpoint: str, timeout: timedelta) -> None:
        nonlocal current_time
        current_time += 15.0  # Cause overrun > 5s timeout
        raise ConnectionResetError("Connection reset after delay")

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=_make_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
        probe_fn=reset_with_delay,
        clock_fn=fake_clock,
        contention_signal_producer=lambda: True,
    )

    with patch.object(bridge, "_perform_restart") as mock_restart:
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        assert bridge._consecutive_probe_failures_deferred == 0
        assert mock_restart.call_count == 1


def test_probe_malformed_response_not_deferred() -> None:
    """Malformed response error is NOT deferred even under fresh contention."""
    inner = _make_inner(poll_result=None, pid=1001)

    def malformed_probe(endpoint: str, timeout: timedelta) -> None:
        raise ValueError("Invalid JSON-RPC response")

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=_make_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
        probe_fn=malformed_probe,
        contention_signal_producer=lambda: True,
    )

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
    inner = _make_inner(poll_result=None, pid=1001)
    call_count = 0

    def probe_fn(endpoint: str, timeout: timedelta) -> None:
        nonlocal call_count
        call_count += 1
        # Calls 1, 2, 3: ordinary probe misses (reaches restart threshold on 3rd miss)
        # Call 4: confirm probe runs (timeout is 2x) -> succeeds!
        if call_count <= 3:
            raise TimeoutError("ordinary probe timed out")
        # 4th call succeeds!

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=_make_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
        probe_fn=probe_fn,
        contention_signal_producer=lambda: False,  # No external contention signal
    )

    with patch.object(bridge, "_perform_restart") as mock_restart:
        # First miss (1/3)
        bridge.check_health_and_restart_if_needed()
        # Second miss (2/3)
        bridge.check_health_and_restart_if_needed()
        # Third miss triggers confirm probe, which succeeds!
        restarted = bridge.check_health_and_restart_if_needed()
        assert restarted is False
        assert mock_restart.call_count == 0
        # Reclassified as deferral
        assert bridge._consecutive_probe_failures_deferred == 1


def test_confirm_before_replace_replaces_on_confirm_failure() -> None:
    """When ordinary probe times out AND confirm probe fails, replacement proceeds."""
    inner = _make_inner(poll_result=None, pid=1001)

    def probe_always_times_out(endpoint: str, timeout: timedelta) -> None:
        raise TimeoutError("probe timed out")

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=_make_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
        probe_fn=probe_always_times_out,
        contention_signal_producer=lambda: False,
    )

    with patch.object(bridge, "_perform_restart") as mock_restart:
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        bridge.check_health_and_restart_if_needed()
        # Probes failed -> restart executed!
        assert mock_restart.call_count == 1


def test_confirm_skipped_when_deferral_ceiling_reached() -> None:
    """When deferral ceiling is reached, confirm probe is skipped and restart forced."""
    inner = _make_inner(poll_result=None, pid=1001)

    probe_calls: list[timedelta] = []

    def probe_fn(endpoint: str, timeout: timedelta) -> None:
        probe_calls.append(timeout)
        raise TimeoutError("probe timed out")

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=_make_inner,
        restart_policy=McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
        probe_fn=probe_fn,
        contention_signal_producer=lambda: True,
    )

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
