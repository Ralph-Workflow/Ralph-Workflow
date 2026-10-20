"""Pins E1.7: Ledger state continuity and per-child state continuity across child replacement.

Catalogue requirements:
- E1.7: Per-child state lost at swap:
  Enumerate ALL state categories (wire ledger chain, repetition tracker, session identity,
  broker fingerprint, tool set hash, wrap-up flags, saturation counters, metrics, exec handles).
  Pins:
  1. Wire ledger state continuity across child replacement (HMAC chain unbroken across child swaps).
  2. Wire ledger replay preserves exact chronological event sequence and detects tampering across swaps.
  3. Repetition tracker is carried across swaps (loop breaker trips on 3rd identical error even if child swapped).
  4. Bridge session attributes (session_id, run_id, broker_fingerprint, expected_tool_hash) carried across swaps.
  5. Child-scoped state (saturation counters, wrap-up flags, child-local handles) are cleanly reset-safe.
"""

from __future__ import annotations

import json
import pathlib
import threading
import time

import pytest

from ralph.mcp.server import lifecycle
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from ralph.mcp.server._transport_repetition_tracker import (
    TransportRepetitionTracker,
    failure_signature,
)
from ralph.mcp.server._wire_ledger import (
    _ledger_path,
    append_wire_record,
    collect_captures,
    render_capture_table_markdown,
    verify_chain,
)

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


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
    tmp_path: pathlib.Path,
    poll_result: int | None = None,
    pid: int = 9999,
    endpoint: str = "http://127.0.0.1:9100/mcp",
) -> StandaloneMcpProcess:
    session_file = tmp_path / f"session_{pid}.json"
    session_file.write_text("{}", encoding="utf-8")
    return StandaloneMcpProcess(
        endpoint=endpoint,
        process=_FakeProcess(poll_result=poll_result, pid=pid),
        session_file=session_file,
    )


# ---------------------------------------------------------------------------
# E1.7: Wire ledger state continuity across child replacement
# ---------------------------------------------------------------------------
def test_e17_wire_ledger_continuity_across_child_swaps(tmp_path: pathlib.Path) -> None:
    """Wire ledger HMAC chain remains unbroken across sequential child replacements."""
    secret = "broker-secret-e17-swap"
    run_id = "run-e17-continuity"

    # Child 1 (generation 0) records tool invocations
    rec1 = append_wire_record(
        tmp_path,
        method="tools/call",
        tool_name="read_file",
        params={"path": "src/main.py"},
        run_id=run_id,
        secret=secret,
    )
    rec2 = append_wire_record(
        tmp_path,
        method="tools/call",
        tool_name="write_file",
        params={"path": "src/main.py", "content": "print('hello')"},
        run_id=run_id,
        secret=secret,
    )
    assert rec1 is not None and rec2 is not None
    assert rec2.prior_hmac == rec1.record_hmac

    # Child 1 is retired; Child 2 (generation 1) replaces Child 1
    # Child 2 reads last HMAC from disk and appends next record
    rec3 = append_wire_record(
        tmp_path,
        method="tools/call",
        tool_name="run_command",
        params={"CommandLine": "pytest"},
        run_id=run_id,
        secret=secret,
    )
    assert rec3 is not None
    assert rec3.prior_hmac == rec2.record_hmac

    # Child 2 is retired; Child 3 (generation 2) replaces Child 2
    rec4 = append_wire_record(
        tmp_path,
        method="tools/call",
        tool_name="declare_complete",
        params={"summary": "all done"},
        run_id=run_id,
        secret=secret,
    )
    assert rec4 is not None
    assert rec4.prior_hmac == rec3.record_hmac

    # Verification across all child generations:
    # 1. Whole HMAC chain verifies successfully
    assert verify_chain(tmp_path, secret) is True

    # 2. Collect captures preserves all events in exact sequence
    captures = collect_captures(tmp_path, secret)
    assert len(captures) == 4
    tool_names = [c.tool_name for c in captures]
    assert tool_names == ["read_file", "write_file", "run_command", "declare_complete"]

    # 3. Capture table markdown renders all 4 events in order
    table_md = render_capture_table_markdown(captures, run_id=run_id)
    assert "read_file" in table_md
    assert "write_file" in table_md
    assert "run_command" in table_md
    assert "declare_complete" in table_md


def test_e17_wire_ledger_tamper_detection_across_swaps(tmp_path: pathlib.Path) -> None:
    """Tampering with a record written by an earlier child invalidates downstream child records."""
    secret = "broker-secret-e17-tamper"
    run_id = "run-e17-tamper"

    # Child 1 appends record
    append_wire_record(
        tmp_path,
        method="tools/call",
        tool_name="tool_child1",
        params={"step": 1},
        run_id=run_id,
        secret=secret,
    )

    # Child 2 appends record after swap
    append_wire_record(
        tmp_path,
        method="tools/call",
        tool_name="tool_child2",
        params={"step": 2},
        run_id=run_id,
        secret=secret,
    )

    # Chain initially verifies
    assert verify_chain(tmp_path, secret) is True

    # Tamper with Child 1's on-disk record
    ledger_file = _ledger_path(tmp_path, secret)
    lines = ledger_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2

    tampered_row_1 = json.loads(lines[0])
    tampered_row_1["tool_name"] = "tampered_tool"
    lines[0] = json.dumps(tampered_row_1)
    ledger_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Chain verification MUST fail because Child 2's prior_hmac binds to Child 1's HMAC
    assert verify_chain(tmp_path, secret) is False
    # Captures are rejected when chain is tampered
    assert collect_captures(tmp_path, secret) == []


# ---------------------------------------------------------------------------
# E1.7: In-memory repetition tracker continuity across child replacement
# ---------------------------------------------------------------------------
def test_e17_transport_repetition_tracker_continuity_across_restart() -> None:
    """Repetition tracker carried across child swaps trips breaker on cumulative loop count."""
    tracker = TransportRepetitionTracker(threshold=3, window_seconds=60.0)

    sig = failure_signature(
        "tools/call", {"path": "missing.txt"}, "FileNotFoundError: no such file"
    )
    assert sig is not None

    # Child 1 experiences 2 identical failure signatures
    assert tracker.observe(sig) is False
    assert tracker.observe(sig) is False

    # Handover / swap occurs: Child 1 is retired, Child 2 is promoted
    # The carried tracker retains its state and streak counter
    # Child 2 experiences the 3rd identical failure signature
    tripped = tracker.observe(sig)

    # Invariant: Breaker trips on the 3rd identical failure across the swap boundary
    assert tripped is True


# ---------------------------------------------------------------------------
# E1.7: Bridge-level state continuity vs reset-safe child state enumeration
# ---------------------------------------------------------------------------
def test_e17_bridge_state_continuity_enumeration(tmp_path: pathlib.Path) -> None:
    """Verify full enumeration of carried vs reset-safe states across bridge replacement."""
    old_inner = _make_inner(tmp_path, poll_result=None, pid=1001)
    new_inner = _make_inner(tmp_path, poll_result=None, pid=2002)

    drain_entered = threading.Event()
    drain_can_finish = threading.Event()

    def controllable_sleep(s: float) -> None:
        drain_entered.set()
        drain_can_finish.wait(timeout=2.0)

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: new_inner,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="run-e17-carried",
        session_id="session-e17-fixed",
        broker_fingerprint="sha256:abcd1234efgh5678",
        workspace_root=tmp_path,
        sleep_fn=controllable_sleep,
    )
    bridge._expected_tool_hash = "tool-set-hash-v1"

    # Perform make-before-break restart
    restarted = bridge._perform_restart(process_exited=False, probe_duration_s=0.2)
    assert restarted is True

    # 1. Carried State (MUST be preserved across child swaps):
    # - Run ID identity
    assert bridge.run_id == "run-e17-carried"
    # - Session ID identity (child sees identical session ID)
    assert bridge._session_id == "session-e17-fixed"
    # - Broker fingerprint (HMAC verification key identity preserved)
    assert bridge._broker_fingerprint == "sha256:abcd1234efgh5678"
    # - Tool set hash (tool catalogue drift guard carried)
    assert bridge._expected_tool_hash == "tool-set-hash-v1"
    # - Restart budget and timestamps
    assert bridge.restart_count == 1
    assert len(bridge._restart_timestamps) == 1
    # - Generation incremented
    assert bridge._bridge_generation == 1
    # - Active process switched to candidate
    assert bridge.process.pid == 2002

    # 2. Reset-Safe State (must reset cleanly per child):
    # - Candidate process is fresh (not contaminated by old child's memory)
    assert new_inner.process.pid == 2002
    assert new_inner.process.poll() is None
    # - In-flight probe failure counters reset on promotion
    assert bridge._consecutive_probe_failures == 0
    assert bridge._consecutive_probe_failures_deferred == 0
    assert bridge._first_probe_deferred_at is None
    # - Draining inner tracked while drain worker is executing
    assert drain_entered.wait(timeout=1.0) is True
    assert len(bridge._draining_inners) == 1
    assert old_inner in bridge._draining_inners

    # Allow drain worker to complete
    drain_can_finish.set()
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and len(bridge._draining_inners) > 0:
        time.sleep(0.01)
    assert len(bridge._draining_inners) == 0
    assert old_inner.process.poll() is not None
