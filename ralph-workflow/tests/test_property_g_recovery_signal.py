from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING, Never

from ralph.agents.execution_state import _helpers as helpers_module
from ralph.agents.execution_state._helpers import _registry_check_for_exit
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import _fallback_http_handler
from ralph.mcp.server._in_memory_transport import drive_request
from ralph.mcp.server._mcp_server import McpServer
from ralph.mcp.server._transport_repetition_tracker import (
    THRESHOLD,
    WINDOW_SECONDS,
    TransportRepetitionTracker,
    signature_for,
)
from ralph.mcp.server.runtime import build_ralph_tool_registry
from ralph.process._alive_by import AliveBy
from ralph.process._child_activity_snapshot import ChildActivitySnapshot
from ralph.process.child_liveness import ChildLivenessRegistry, classify_child_snapshot

if TYPE_CHECKING:
    from ralph.mcp.server._json_rpc_request import JsonRpcRequest
    from ralph.mcp.server._server_state import ServerState


def test_os_descendant_only_stale_progress_does_not_defer() -> None:
    snapshot = ChildActivitySnapshot(
        scope_prefix="agent:",
        has_process=False,
        has_fresh_label=False,
        has_fresh_heartbeat=False,
        has_fresh_progress=False,
        oldest_live_child_seconds=None,
        active_count=0,
        terminal_count=0,
    )
    verdict = classify_child_snapshot(snapshot, has_os_descendants=True)
    assert verdict.alive_by == AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS
    assert verdict.deferral_allowed is False, (
        f"OS_DESCENDANT_ONLY_STALE_PROGRESS must NOT defer; got {verdict.deferral_allowed}"
    )


def test_property_g_watchdog_caller_refuses_descendant_deferral() -> None:
    snapshot = ChildActivitySnapshot(
        scope_prefix="agent:",
        has_process=False,
        has_fresh_label=False,
        has_fresh_heartbeat=False,
        has_fresh_progress=False,
        oldest_live_child_seconds=None,
        active_count=0,
        terminal_count=0,
    )

    class _FakeRegistry(ChildLivenessRegistry):
        def snapshot(self, scope_prefix: str) -> ChildActivitySnapshot:
            return snapshot

        def has_records(self, scope_prefix: str) -> bool:
            return False

    registry = _FakeRegistry(
        progress_ttl=60.0,
        heartbeat_ttl=60.0,
        stale_label_ttl=60.0,
        exit_reconcile=60.0,
    )

    original_classify = helpers_module.classify_child_snapshot
    helpers_module.classify_child_snapshot = lambda snap, has_os_descendants=True: (
        original_classify(snap, has_os_descendants=True)
    )
    try:
        decided_state, scoped_stale = _registry_check_for_exit(registry, "agent:")
    finally:
        helpers_module.classify_child_snapshot = original_classify
    assert decided_state is None, (
        f"watchdog caller must NOT extend the deadline on "
        f"OS_DESCENDANT_ONLY_STALE_PROGRESS; got {decided_state}"
    )
    assert scoped_stale is False


def test_os_descendant_zero_progress_no_defer_with_empty_snapshot() -> None:
    snapshot = ChildActivitySnapshot(
        scope_prefix="agent:",
        has_process=False,
        has_fresh_label=False,
        has_fresh_heartbeat=False,
        has_fresh_progress=False,
        oldest_live_child_seconds=None,
        active_count=0,
        terminal_count=0,
    )
    verdict = classify_child_snapshot(snapshot, has_os_descendants=False)
    assert verdict.alive_by is None
    assert verdict.deferral_allowed is False


def test_fresh_progress_does_still_defer() -> None:
    snapshot = ChildActivitySnapshot(
        scope_prefix="agent:",
        has_process=True,
        has_fresh_label=True,
        has_fresh_heartbeat=True,
        has_fresh_progress=True,
        oldest_live_child_seconds=1.0,
        active_count=1,
        terminal_count=0,
    )
    verdict = classify_child_snapshot(snapshot, has_os_descendants=False)
    assert verdict.alive_by == AliveBy.FRESH_PROGRESS
    assert verdict.deferral_allowed is True


def test_transport_tracker_threshold_is_three() -> None:
    assert THRESHOLD == 3
    assert WINDOW_SECONDS == 60.0


def test_transport_tracker_trips_on_three_identical() -> None:
    tracker = TransportRepetitionTracker()
    assert tracker.observe("sig-A") is False
    assert tracker.observe("sig-A") is False
    assert tracker.observe("sig-A") is True


def test_transport_tracker_resets_on_different_signature() -> None:
    tracker = TransportRepetitionTracker()
    tracker.observe("sig-A")
    tracker.observe("sig-A")
    assert tracker.observe("sig-B") is False
    assert tracker.observe("sig-A") is False


def test_transport_tracker_window_expiry_resets_streak() -> None:
    tracker = TransportRepetitionTracker()
    tracker.observe("sig-A")
    tracker.observe("sig-A")
    tracker._last_seen_at -= 100.0
    assert tracker.observe("sig-A") is False


def test_signature_strips_uuid_hex_tokens() -> None:
    sig_a = signature_for("TimeoutError: 12345678abcdef01 at line 5")
    sig_b = signature_for("TimeoutError: 87654321fedcba09 at line 5")
    assert sig_a == sig_b, f"UUIDs must be normalized: {sig_a!r} vs {sig_b!r}"


def test_signature_strips_iso_timestamps() -> None:
    sig_a = signature_for("Error at 2024-01-15T12:34:56Z happened")
    sig_b = signature_for("Error at 2024-02-20T08:00:00Z happened")
    assert sig_a == sig_b


def test_signature_strips_hms_clocks() -> None:
    sig_a = signature_for("Error at 12:34:56 happened")
    sig_b = signature_for("Error at 23:59:59 happened")
    assert sig_a == sig_b


def test_signature_strips_request_id() -> None:
    sig_a = signature_for("Error: request_id=abc123 happened")
    sig_b = signature_for("Error: request_id=xyz789 happened")
    assert sig_a == sig_b


def test_signature_preserves_nonvolatile_text() -> None:
    sig_a = signature_for("ConnectionError: agent wedged")
    sig_b = signature_for("ConnectionError: agent recovered")
    assert sig_a != sig_b


def test_signature_for_exception_uses_type_and_str() -> None:

    class _UniqueError(Exception):
        pass

    exc_a = _UniqueError("arg-X")
    exc_b = _UniqueError("arg-Y")
    assert signature_for(exc_a) != signature_for(exc_b)

    exc_c = _UniqueError("arg-X")
    assert signature_for(exc_a) == signature_for(exc_c)


def test_transport_repetition_tracker_is_thread_safe() -> None:
    tracker = TransportRepetitionTracker()
    results: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        result = tracker.observe("same-sig")
        with lock:
            results.append(result)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for r in results if r) >= 1


def test_transport_repetition_tracker_snapshot() -> None:
    tracker = TransportRepetitionTracker()
    tracker.observe("X")
    snap = tracker.snapshot()
    assert snap["streak"] == 1
    assert snap["last_signature"] == "X"
    assert snap["threshold"] == 3


def test_transport_repetition_tracker_reset() -> None:
    tracker = TransportRepetitionTracker()
    tracker.observe("X")
    tracker.observe("X")
    tracker.reset()
    snap = tracker.snapshot()
    assert snap["streak"] == 0
    assert snap["last_signature"] is None


def test_transport_loop_detected_returns_503_with_specific_message() -> None:

    fresh = TransportRepetitionTracker()
    _fallback_http_handler._transport_repetition_tracker = fresh

    session = AgentSession(
        session_id="g-test",
        run_id="g-run",
        drain="standalone",
        capabilities={"WorkspaceRead"},
    )
    workspace = _FakeWorkspace()
    registry = build_ralph_tool_registry(session, workspace)
    mcp_server = McpServer(session, workspace, registry)

    def explode(server: McpServer, request: JsonRpcRequest, state: ServerState) -> Never:
        raise TimeoutError("aabbccdd-1234-5678-90ab-cdef12345678 at 12:34:56")

    mcp_server._dispatch_request = lambda req, state: explode(mcp_server, req, state)

    statuses: list[int] = []
    bodies: list[bytes] = []
    for i in range(3):
        status, _headers, body = drive_request(
            mcp_server,
            json.dumps({"jsonrpc": "2.0", "id": i, "method": "tools/list", "params": {}}).encode(),
        )
        statuses.append(status)
        bodies.append(body)
    assert statuses[2] == 503
    payload = json.loads(bodies[2])
    assert payload["error"]["code"] == -32001
    assert "transport_loop_detected" in payload["error"]["message"]


class _FakeWorkspace:
    def __init__(self) -> None:
        pass
