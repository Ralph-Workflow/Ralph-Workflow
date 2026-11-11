from __future__ import annotations

import contextlib
import json
import pathlib
import sys
import threading
import time
from typing import Protocol

import pytest

from ralph.mcp.protocol.env import MCP_SNAPSHOT_DIR_ENV
from ralph.mcp.server import lifecycle
from ralph.mcp.server._handover_protocol import compute_tool_set_hash
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
from ralph.mcp.upstream.registry import UpstreamRegistry
from ralph.process.manager import SpawnOptions, get_process_manager

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]

_CARRIED_TOOL_ENTRIES: list[dict[str, object]] = [
    {"name": "e17_carried_tool", "description": "Carried across the child swap", "inputSchema": {}},
]


def _served_marker(registry: UpstreamRegistry, alias: str) -> str:
    result = registry.call_tool(alias, {})
    assert isinstance(result, dict)
    marker = result.get("marker")
    assert isinstance(marker, str)
    return marker


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

    def mark_exited(self, code: int = 0) -> None:
        self._poll_result = code

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


def test_e17_wire_ledger_continuity_across_child_swaps(tmp_path: pathlib.Path) -> None:
    secret = "broker-secret-e17-swap"
    run_id = "run-e17-continuity"

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

    assert verify_chain(tmp_path, secret) is True

    captures = collect_captures(tmp_path, secret)
    assert len(captures) == 4
    tool_names = [c.tool_name for c in captures]
    assert tool_names == ["read_file", "write_file", "run_command", "declare_complete"]

    table_md = render_capture_table_markdown(captures, run_id=run_id)
    assert "read_file" in table_md
    assert "write_file" in table_md
    assert "run_command" in table_md
    assert "declare_complete" in table_md


def test_e17_wire_ledger_tamper_detection_across_swaps(tmp_path: pathlib.Path) -> None:
    secret = "broker-secret-e17-tamper"
    run_id = "run-e17-tamper"

    append_wire_record(
        tmp_path,
        method="tools/call",
        tool_name="tool_child1",
        params={"step": 1},
        run_id=run_id,
        secret=secret,
    )

    append_wire_record(
        tmp_path,
        method="tools/call",
        tool_name="tool_child2",
        params={"step": 2},
        run_id=run_id,
        secret=secret,
    )

    assert verify_chain(tmp_path, secret) is True

    ledger_file = _ledger_path(tmp_path, secret)
    lines = ledger_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2

    tampered_row_1 = json.loads(lines[0])
    tampered_row_1["tool_name"] = "tampered_tool"
    lines[0] = json.dumps(tampered_row_1)
    ledger_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert verify_chain(tmp_path, secret) is False
    assert collect_captures(tmp_path, secret) == []


_LOOP_CALLER_ERROR = "Tool 'read_file' failed: unsupported encoding 'utf-77'"

_LOOP_CALL = {"name": "read_file", "arguments": {"path": ".agent/PLAN.md"}}


def _loop_signature() -> str:
    signature = failure_signature("tools/call", _LOOP_CALL, _LOOP_CALLER_ERROR)
    assert signature is not None, "a caller-side failure is countable, never exempt"
    return signature


def _parent_owned_snapshot_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    snap_dir = tmp_path / "mcp-snapshots" / "run-e17-repetition"
    snap_dir.mkdir(parents=True)
    return snap_dir


def test_e17_transport_loop_breaker_trips_across_child_replacement(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MCP_SNAPSHOT_DIR_ENV, str(_parent_owned_snapshot_dir(tmp_path)))
    signature = _loop_signature()

    old_child = TransportRepetitionTracker.from_parent_owned_state()
    assert old_child.observe(signature) is False
    assert old_child.observe(signature) is False

    candidate_child = TransportRepetitionTracker.from_parent_owned_state()
    assert candidate_child is not old_child

    assert candidate_child.observe(signature) is True


def test_e17_transport_loop_breaker_starts_fresh_without_the_parent_owned_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(MCP_SNAPSHOT_DIR_ENV, raising=False)
    signature = _loop_signature()

    old_child = TransportRepetitionTracker.from_parent_owned_state()
    assert old_child.observe(signature) is False
    assert old_child.observe(signature) is False

    candidate_child = TransportRepetitionTracker.from_parent_owned_state()
    assert candidate_child is not old_child

    assert candidate_child.observe(signature) is False


def test_e17_transport_loop_breaker_reset_clears_the_carried_streak(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MCP_SNAPSHOT_DIR_ENV, str(_parent_owned_snapshot_dir(tmp_path)))
    signature = _loop_signature()

    old_child = TransportRepetitionTracker.from_parent_owned_state()
    assert old_child.observe(signature) is False
    assert old_child.observe(signature) is False
    old_child.reset()

    candidate_child = TransportRepetitionTracker.from_parent_owned_state()
    assert candidate_child is not old_child
    assert candidate_child.observe(signature) is False


def test_e17_bridge_state_continuity_enumeration(tmp_path: pathlib.Path) -> None:
    old_inner = _make_inner(tmp_path, poll_result=None, pid=1001)
    new_inner = _make_inner(tmp_path, poll_result=None, pid=2002)

    drain_entered = threading.Event()
    drain_can_finish = threading.Event()

    def controllable_sleep(s: float) -> None:
        drain_entered.set()
        drain_can_finish.wait(timeout=2.0)

    probe_should_fail = [False]

    def probe_fn(endpoint: str, timeout: object) -> None:
        if probe_should_fail[0]:
            raise RuntimeError("probe failed")

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: new_inner,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="run-e17-carried",
        session_id="session-e17-fixed",
        broker_fingerprint="sha256:abcd1234efgh5678",
        workspace_root=tmp_path,
        sleep_fn=controllable_sleep,
        tool_entries_fn=lambda endpoint, timeout: list(_CARRIED_TOOL_ENTRIES),
        probe_fn=probe_fn,
        contention_signal_producer=lambda: False,
    )
    bridge.capture_tool_hash_baseline()
    carried_tool_hash = compute_tool_set_hash(_CARRIED_TOOL_ENTRIES)

    probe_should_fail[0] = True
    restarted = False
    for _ in range(5):
        restarted = bridge.check_health_and_restart_if_needed()
        if restarted:
            break
    assert restarted is True

    assert bridge.run_id == "run-e17-carried"
    assert bridge.diagnostics().session_id == "session-e17-fixed"
    assert bridge.diagnostics().broker_fingerprint == "sha256:abcd1234efgh5678"
    assert bridge.diagnostics().tool_set_hash == carried_tool_hash
    assert bridge.restart_count == 1
    assert bridge.diagnostics().restart_attempt_count == 1
    assert bridge.diagnostics().generation == 1
    assert bridge.process.pid == 2002
    assert bridge.diagnostics().broker_fingerprint is not None

    assert new_inner.process.pid == 2002
    assert new_inner.process.poll() is None
    assert bridge.diagnostics().consecutive_probe_failures == 0
    assert bridge.diagnostics().deferred_probe_failures == 0
    assert bridge.diagnostics().first_probe_deferred is False
    assert drain_entered.wait(timeout=1.0) is True
    assert bridge.diagnostics().draining_process_count == 1

    drain_can_finish.set()
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and bridge.diagnostics().draining_process_count > 0:
        time.sleep(0.02)
    assert bridge.diagnostics().draining_process_count == 0
    assert old_inner.process.poll() is not None


def test_e17_wrap_up_flags_reset_safe_across_child_swap(tmp_path: pathlib.Path) -> None:
    from ralph.mcp.server._session_wrapup import (
        session_before_warning,
        session_warning_fired,
        session_warning_scope,
    )

    old_inner = _make_inner(tmp_path, poll_result=None, pid=1001)
    new_inner = _make_inner(tmp_path, poll_result=None, pid=2002)
    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: new_inner,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="run-e17-wrapup",
        sleep_fn=lambda s: None,
    )

    with session_warning_scope(False):
        assert session_before_warning() is False
        assert session_warning_fired() is True

        old_inner.process.terminate()
        restarted = bridge.check_health_and_restart_if_needed()
        assert restarted is True

    assert session_before_warning() is False
    assert session_warning_fired() is False

    other_thread_default: list[bool | None] = [None]

    def _observer() -> None:
        other_thread_default.append(session_before_warning())

    t = threading.Thread(target=_observer)
    t.start()
    t.join()
    assert other_thread_default[-1] is False


def test_e17_saturation_counters_reset_safe_across_child_swap(
    tmp_path: pathlib.Path,
) -> None:
    import ralph.mcp.server._saturated_dispatch as saturated_dispatch

    old_inner = _make_inner(tmp_path, poll_result=None, pid=1001)
    new_inner = _make_inner(tmp_path, poll_result=None, pid=2002)
    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: new_inner,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="run-e17-saturation",
        sleep_fn=lambda s: None,
    )

    class _SaturatedDispatchProbe(Protocol):
        _inflight: int
        _lock: threading.Lock

    dispatcher: _SaturatedDispatchProbe = saturated_dispatch._default_dispatch
    with dispatcher._lock:
        prior_inflight = dispatcher._inflight
        dispatcher._inflight += 1
    try:
        inflight_before_swap = dispatcher._inflight
        assert inflight_before_swap == prior_inflight + 1

        old_inner.process.terminate()
        restarted = bridge.check_health_and_restart_if_needed()
        assert restarted is True
        inflight_after_swap = dispatcher._inflight
        assert inflight_after_swap == prior_inflight + 1

        saturated_dispatch.reset_default()
        fresh_dispatcher: _SaturatedDispatchProbe = saturated_dispatch._default_dispatch
        fresh_inflight = fresh_dispatcher._inflight
        assert fresh_inflight == 0
    finally:
        with dispatcher._lock:
            dispatcher._inflight = prior_inflight


def test_e17_metrics_reset_safe_across_child_swap(tmp_path: pathlib.Path) -> None:
    from ralph.mcp.server._metrics import get_default_metrics, reset_default_metrics

    old_inner = _make_inner(tmp_path, poll_result=None, pid=1001)
    new_inner = _make_inner(tmp_path, poll_result=None, pid=2002)
    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: new_inner,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="run-e17-metrics",
        sleep_fn=lambda s: None,
    )

    metrics = get_default_metrics()
    metrics.record_post_header_failure(
        request_id="e17-old",
        method="tools/call",
        session_impl="AgentSession",
        cause="simulated-old-child-failure",
    )
    metrics.record_terminal_frame("exec")
    baseline = metrics.snapshot()
    assert baseline["post_header_failures"] >= 1
    assert baseline["terminal_frame_emissions"] >= 1

    old_inner.process.terminate()
    restarted = bridge.check_health_and_restart_if_needed()
    assert restarted is True

    reset_default_metrics()
    fresh_metrics = get_default_metrics()
    fresh_snapshot = fresh_metrics.snapshot()
    assert fresh_snapshot["post_header_failures"] == 0
    assert fresh_snapshot["terminal_frame_emissions"] == 0


def test_e17_upstream_sessions_reset_safe_across_child_swap(
    tmp_path: pathlib.Path,
) -> None:
    from ralph.mcp.upstream.config import UpstreamMcpServer
    from ralph.mcp.upstream.models import UpstreamTool

    old_inner = _make_inner(tmp_path, poll_result=None, pid=1001)
    new_inner = _make_inner(tmp_path, poll_result=None, pid=2002)

    class _OldUpstreamClient:
        server_marker = "old"

        def list_tools(self, *, timeout_s: float | None = None) -> list[UpstreamTool]:
            del timeout_s
            return []

        def call_tool(self, name: str, arguments: dict[str, object]) -> object:
            del name, arguments
            return {"content": [], "marker": self.server_marker}

    old_server = UpstreamMcpServer(
        name="e17-old-upstream",
        transport="stdio",
        command="echo",
        args=("noop",),
    )
    old_registry = UpstreamRegistry.build_from_tool_catalog(
        [old_server],
        {
            "e17-old-upstream": [
                UpstreamTool(name="e17_old_tool_1", description="OLD child tool #1"),
                UpstreamTool(name="e17_old_tool_2", description="OLD child tool #2"),
            ],
        },
        client_factory=lambda _s: _OldUpstreamClient(),
    )

    old_tools_before = sorted(proxied.tool.name for proxied in old_registry.tool_definitions())
    assert old_tools_before == ["e17_old_tool_1", "e17_old_tool_2"], old_tools_before
    old_aliases = [proxied.alias for proxied in old_registry.tool_definitions()]
    assert [_served_marker(old_registry, alias) for alias in old_aliases] == ["old", "old"]

    class _NewUpstreamClient:
        server_marker = "new"

        def list_tools(self, *, timeout_s: float | None = None) -> list[UpstreamTool]:
            del timeout_s
            return []

        def call_tool(self, name: str, arguments: dict[str, object]) -> object:
            del name, arguments
            return {"content": [], "marker": self.server_marker}

    new_server = UpstreamMcpServer(
        name="e17-new-upstream",
        transport="stdio",
        command="echo",
        args=("noop",),
    )
    new_registry = UpstreamRegistry.build_from_tool_catalog(
        [new_server],
        {
            "e17-new-upstream": [
                UpstreamTool(name="e17_new_tool_1", description="NEW child tool #1"),
            ],
        },
        client_factory=lambda _s: _NewUpstreamClient(),
    )

    new_tools_before = sorted(proxied.tool.name for proxied in new_registry.tool_definitions())
    assert new_tools_before == ["e17_new_tool_1"], new_tools_before
    new_aliases = [proxied.alias for proxied in new_registry.tool_definitions()]
    assert [_served_marker(new_registry, alias) for alias in new_aliases] == ["new"]

    assert old_registry is not new_registry
    assert {proxied.server_name for proxied in old_registry.tool_definitions()} == {
        "e17-old-upstream"
    }
    assert {proxied.server_name for proxied in new_registry.tool_definitions()} == {
        "e17-new-upstream"
    }
    assert set(old_aliases).isdisjoint(new_aliases)
    assert all(_served_marker(old_registry, alias) == "old" for alias in old_aliases)
    assert all(_served_marker(new_registry, alias) == "new" for alias in new_aliases)

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: new_inner,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="run-e17-upstream",
        sleep_fn=lambda s: None,
    )
    old_inner.process.terminate()
    restarted = bridge.check_health_and_restart_if_needed()
    assert restarted is True
    assert bridge.diagnostics().generation == 1
    assert bridge.process.pid == 2002

    old_tools_after = sorted(proxied.tool.name for proxied in old_registry.tool_definitions())
    new_tools_after = sorted(proxied.tool.name for proxied in new_registry.tool_definitions())
    assert old_tools_after == old_tools_before, (
        f"OLD upstream registry mutated across swap: {old_tools_after!r} != {old_tools_before!r}"
    )
    assert new_tools_after == new_tools_before, (
        f"NEW upstream registry mutated across swap: {new_tools_after!r} != {new_tools_before!r}"
    )
    assert "e17-old-upstream" not in {
        proxied.server_name for proxied in new_registry.tool_definitions()
    }, "NEW upstream registry inherited OLD's upstream client key after a child swap"
    assert "e17-new-upstream" not in {
        proxied.server_name for proxied in old_registry.tool_definitions()
    }, "OLD upstream registry acquired NEW's upstream client key after a child swap"
    assert all(_served_marker(old_registry, alias) == "old" for alias in old_aliases)
    assert all(_served_marker(new_registry, alias) == "new" for alias in new_aliases)


def test_e17_exec_resource_handle_reset_safe_across_child_swap(
    tmp_path: pathlib.Path,
) -> None:
    from ralph.mcp.server._json_rpc_request import JsonRpcRequest
    from ralph.mcp.server._mcp_server import McpServer
    from ralph.mcp.server._server_state import ServerState
    from ralph.mcp.server.runtime_session import FileBackedSession
    from ralph.mcp.tools._exec_resource_uri import ExecResourceResolver
    from ralph.mcp.tools.bridge._registry import build_ralph_tool_registry
    from ralph.workspace.memory import MemoryWorkspace

    old_inner = _make_inner(tmp_path, poll_result=None, pid=1001)
    new_inner = _make_inner(tmp_path, poll_result=None, pid=2002)

    old_spill_root = tmp_path / "e17_old_spills"
    old_spill_root.mkdir(parents=True, exist_ok=True)
    old_session_file = tmp_path / "e17_old_session.json"
    old_session_file.write_text(
        json.dumps(
            {
                "session_id": "session-e17-exec-old",
                "run_id": "run-e17-exec-old",
                "capabilities": ["ProcessExecBounded"],
                "exec_spill_roots": [str(old_spill_root)],
            }
        ),
        encoding="utf-8",
    )
    old_resolver = ExecResourceResolver(spill_roots=(old_spill_root,))
    old_session = FileBackedSession(
        old_session_file,
        workspace_root=tmp_path,
        exec_resource_resolver=old_resolver,
    )

    old_spill = old_spill_root / "ralph-exec-e17old.txt"
    old_payload = b"OLD-child spill body: recoverable via ralph://exec/"
    old_spill.write_bytes(old_payload)
    old_uri = old_resolver.register(old_spill)
    assert old_uri.startswith("ralph://exec/"), old_uri
    assert old_session.exec_resource_resolver is old_resolver

    new_session_file = tmp_path / "e17_new_session.json"
    new_session_file.write_text(
        json.dumps(
            {
                "session_id": "session-e17-exec-new",
                "run_id": "run-e17-exec-new",
                "capabilities": ["ProcessExecBounded"],
            }
        ),
        encoding="utf-8",
    )
    new_session = FileBackedSession(
        new_session_file,
        workspace_root=tmp_path,
    )
    assert new_session.exec_resource_resolver is None

    ws_old = MemoryWorkspace(tmp_path)
    handler_old = McpServer(
        session=old_session,
        workspace=ws_old,
        registry=build_ralph_tool_registry(old_session, ws_old),
    )
    old_request = JsonRpcRequest(
        jsonrpc="2.0",
        method="resources/read",
        params={"uri": old_uri},
        msg_id=1,
    )
    old_response, _state_old = handler_old.handle_request(old_request, ServerState.RUNNING)
    assert old_response is not None
    assert old_response.error is None, (
        f"OLD child should replay {old_uri!r} via resources/read: {old_response.error}"
    )
    old_result = old_response.result
    assert isinstance(old_result, dict)
    old_first = old_result["contents"][0]
    assert isinstance(old_first, dict)
    assert old_first["uri"] == old_uri
    import base64 as _base64

    assert _base64.b64decode(old_first["blob"]) == old_payload

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: new_inner,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="run-e17-exec",
        sleep_fn=lambda s: None,
    )
    old_inner.process.terminate()
    restarted = bridge.check_health_and_restart_if_needed()
    assert restarted is True
    assert bridge.diagnostics().generation == 1

    ws_new = MemoryWorkspace(tmp_path)
    handler_new = McpServer(
        session=new_session,
        workspace=ws_new,
        registry=build_ralph_tool_registry(new_session, ws_new),
    )
    new_request = JsonRpcRequest(
        jsonrpc="2.0",
        method="resources/read",
        params={"uri": old_uri},
        msg_id=2,
    )
    new_response, _state_new = handler_new.handle_request(new_request, ServerState.RUNNING)
    assert new_response is not None
    assert new_response.error is not None, (
        f"NEW child should return a structured error for OLD {old_uri!r}; "
        f"got result={new_response.result!r}"
    )
    new_error = new_response.error
    assert new_error["code"] == -32602, (
        f"NEW child must return the structured JSON-RPC error code -32602 "
        f"for an unsupported exec URI after a swap; got code="
        f"{new_error['code']!r} message={new_error['message']!r}"
    )
    assert "Exec spill resolver is not attached" in new_error["message"], (
        f"NEW child returned an unexpected error message for an OLD URI "
        f"after the swap; expected the typed 'Exec spill resolver is not "
        f"attached' message, got {new_error['message']!r}"
    )

    post_old_response, _post_state = handler_old.handle_request(old_request, ServerState.RUNNING)
    assert post_old_response is not None
    assert post_old_response.error is None, (
        f"OLD child resolver was unexpectedly erased across the swap: "
        f"error={post_old_response.error!r}"
    )
    assert isinstance(post_old_response.result, dict)
    post_old_first = post_old_response.result["contents"][0]
    assert _base64.b64decode(post_old_first["blob"]) == old_payload


def test_e17_repetition_tracker_carried_via_bridge_swap_with_fake_inners(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MCP_SNAPSHOT_DIR_ENV, str(_parent_owned_snapshot_dir(tmp_path)))
    signature = _loop_signature()

    old_inner = _make_inner(tmp_path, poll_result=None, pid=1001)
    new_inner = _make_inner(tmp_path, poll_result=None, pid=2002)
    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: new_inner,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="run-e17-carried-tracker",
        sleep_fn=lambda s: None,
    )

    old_tracker = TransportRepetitionTracker.from_parent_owned_state()
    assert old_tracker.observe(signature) is False
    assert old_tracker.observe(signature) is False

    old_inner.process.terminate()
    restarted = bridge.check_health_and_restart_if_needed()
    assert restarted is True

    new_tracker = TransportRepetitionTracker.from_parent_owned_state()
    assert new_tracker is not old_tracker
    assert new_tracker.observe(signature) is True


def test_e17_repetition_tracker_carried_across_real_subprocess_swap(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(MCP_SNAPSHOT_DIR_ENV, str(_parent_owned_snapshot_dir(tmp_path)))
    signature = _loop_signature()

    pm = get_process_manager()

    old_script = tmp_path / "e17_real_old.py"
    old_script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    old_proc = pm.spawn(
        [sys.executable, str(old_script)],
        SpawnOptions(cwd=str(tmp_path), label="e17-real-old-child"),
    )

    new_script = tmp_path / "e17_real_new.py"
    new_script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    new_proc = pm.spawn(
        [sys.executable, str(new_script)],
        SpawnOptions(cwd=str(tmp_path), label="e17-real-new-child"),
    )

    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if old_proc.poll() is None and new_proc.poll() is None:
                break
            time.sleep(0.02)
        assert old_proc.poll() is None, (
            f"real OLD subprocess died before swap; poll={old_proc.poll()}"
        )
        assert new_proc.poll() is None, (
            f"real NEW subprocess died before swap; poll={new_proc.poll()}"
        )
        assert old_proc.pid != new_proc.pid, (
            "real OLD and NEW subprocesses share a pid (impossible)"
        )

        session_file = tmp_path / "session_e17_real.json"
        session_file.write_text("{}", encoding="utf-8")

        old_inner = StandaloneMcpProcess(
            "http://127.0.0.1:9100/mcp",
            old_proc,
            session_file,
            stage_id="stage-real-old",
        )
        new_inner = StandaloneMcpProcess(
            "http://127.0.0.1:9101/mcp",
            new_proc,
            session_file,
            stage_id="stage-real-new",
        )

        bridge = lifecycle.RestartAwareMcpBridge(
            old_inner,
            restart_fn=lambda: new_inner,
            restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
            run_id="run-e17-real-tracker",
            workspace_root=tmp_path,
            sleep_fn=lambda s: None,
        )

        old_tracker = TransportRepetitionTracker.from_parent_owned_state()
        assert old_tracker.observe(signature) is False
        assert old_tracker.observe(signature) is False

        old_inner.process.terminate()
        restarted = bridge.check_health_and_restart_if_needed()
        assert restarted is True
        assert bridge.process.pid == new_proc.pid, (
            f"bridge did not adopt real NEW subprocess: pid={bridge.process.pid}, "
            f"expected={new_proc.pid}"
        )
        assert bridge.diagnostics().generation == 1

        new_tracker = TransportRepetitionTracker.from_parent_owned_state()
        assert new_tracker is not old_tracker
        assert new_tracker.observe(signature) is True

        assert old_proc.poll() is not None, "OLD real subprocess was not reaped after bridge swap"
        assert new_proc.poll() is None, "NEW real subprocess died during bridge swap"
    finally:
        if old_proc.poll() is None:
            with contextlib.suppress(Exception):
                old_proc.kill()
                old_proc.wait(timeout=2.0)
        if new_proc.poll() is None:
            with contextlib.suppress(Exception):
                new_proc.kill()
                new_proc.wait(timeout=2.0)
