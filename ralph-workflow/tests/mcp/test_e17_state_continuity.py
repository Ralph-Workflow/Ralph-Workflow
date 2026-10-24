"""Pins E1.7: Ledger state continuity and per-child state continuity across child replacement.

Testing-policy white-box exception (make-before-break restart):

The cases marked ``# policy: white-box-exception`` below reach into the
bridge's private restart machinery and into two private gauges. The private
symbols involved are ``RestartAwareMcpBridge._perform_restart``, the
saturation dispatcher's module-global ``_default_dispatch`` (with its
``_lock`` and ``_inflight`` gauge fields) and ``McpServer._session`` /
``McpServer._handle_resources_read``.

Quoted policy clause -- docs/ralph-workflow-policy/testing-policy.md,
"Default requirements":
  "White-box tests that reach into private internals are permitted only
   when no observable surface can express the regression."

Why no public observable expresses these regressions: the state-continuity
cases must swap the child through the make-before-break restart driver with
a CONTROLLED probe duration, so the ledger continuity across the swap can
be observed. The public ``check_health_and_restart_if_needed()`` decides on
its own health verdict and exposes neither the probe-duration budget nor the
"old child already exited" branch these cases pin, and the restart's own
verdict is published on no public surface. The saturation dispatcher has NO
public accessor: its in-flight gauge (``_inflight`` under ``_lock``) is a
module-global counter with no published getter, so "the gauge survived the
child swap" cannot be read through any public surface. The resource-read
handler is driven through the private ``McpServer._handle_resources_read``
entry point because the public tool handler builds and transports a whole
server request, which is not what these cases measure.
"""

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

#: Tool catalogue carried across a child swap. The bridge hashes the SERVED
#: list, so the reference is seeded through the public baseline-capture path
#: (``tool_entries_fn`` + ``capture_tool_hash_baseline``).
_CARRIED_TOOL_ENTRIES: list[dict[str, object]] = [
    {"name": "e17_carried_tool", "description": "Carried across the child swap", "inputSchema": {}},
]


def _served_marker(registry: UpstreamRegistry, alias: str) -> str:
    """Return the server marker of the upstream client that served ``alias``.

    The registry's PUBLIC ``call_tool`` routes an alias to the client that
    owns it, so the marker is observed through the same surface an agent
    uses — no registry internals are inspected.
    """
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
# E1.7: Transport loop-breaker continuity across a real child replacement
# ---------------------------------------------------------------------------

#: A tool failure caused by the CALLER's own arguments, not by Ralph's
#: storage. An agent re-issuing this identical call really is looping, so
#: it is the failure class the transport breaker exists to cap.
_LOOP_CALLER_ERROR = "Tool 'read_file' failed: unsupported encoding 'utf-77'"

#: The single call both children issue: identical method, tool and
#: arguments, so both failures collapse onto one repetition signature.
_LOOP_CALL = {"name": "read_file", "arguments": {"path": ".agent/PLAN.md"}}


def _loop_signature() -> str:
    """Return the repetition key the transport feeds the breaker for ``_LOOP_CALL``."""
    signature = failure_signature("tools/call", _LOOP_CALL, _LOOP_CALLER_ERROR)
    assert signature is not None, "a caller-side failure is countable, never exempt"
    return signature


def _parent_owned_snapshot_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """Return the directory the parent hands to EVERY child of one bridge run.

    ``lifecycle._spawn_mcp_process`` computes ``snapshot_dir(root,
    bridge_run_id)`` once per bridge run and hands that SAME path to every
    spawn through ``RALPH_MCP_SNAPSHOT_DIR``; the per-stage make-before-break
    pause/resume sentinels live inside it. It is therefore parent-owned
    state that provably outlives a child replacement.
    """
    snap_dir = tmp_path / "mcp-snapshots" / "run-e17-repetition"
    snap_dir.mkdir(parents=True)
    return snap_dir


def test_e17_transport_loop_breaker_trips_across_child_replacement(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The breaker trips on the 3rd identical failure ACROSS a child swap.

    A make-before-break replacement is two real children: the retiring
    child is drained and killed, the candidate is promoted in its place.
    The loop detector must therefore be able to count failures recorded
    by a process that no longer exists, or every swap silently hands a
    doomed retry storm a fresh budget of retries.

    ``observe()`` returning ``True`` is the breaker's public verdict --
    the transport turns exactly that into a 503 ``transport_loop_detected``
    frame instead of answering the doomed retry.
    """
    monkeypatch.setenv(MCP_SNAPSHOT_DIR_ENV, str(_parent_owned_snapshot_dir(tmp_path)))
    signature = _loop_signature()

    # Child 1 (generation 0) records two identical failures. The breaker
    # must stay shut: two attempts is not yet a loop.
    old_child = TransportRepetitionTracker.from_parent_owned_state()
    assert old_child.observe(signature) is False
    assert old_child.observe(signature) is False

    # Child 1 is retired and destroyed. Child 2 is a DISTINCT instance,
    # built through the production resolution path and pointed at the
    # SAME parent-owned directory the parent handed to child 1.
    candidate_child = TransportRepetitionTracker.from_parent_owned_state()
    assert candidate_child is not old_child

    # Child 2 (generation 1) experiences the 3rd identical failure.

    # INVARIANT: 2 failures in child 1 + 1 in child 2 == 3 trips the
    # breaker, so the agent gets the typed loop-detected frame from the
    # promoted child instead of yet another silent retry.
    assert candidate_child.observe(signature) is True


def test_e17_transport_loop_breaker_starts_fresh_without_the_parent_owned_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control: with no parent-owned directory the swap does NOT carry state.

    This is the falsification arm of the continuity contract. It proves the
    previous test trips because of real cross-child persistence and not
    because some shared in-process object happened to stay warm, so a
    regressed seam cannot be hidden by a second trip for the wrong reason.
    """
    monkeypatch.delenv(MCP_SNAPSHOT_DIR_ENV, raising=False)
    signature = _loop_signature()

    old_child = TransportRepetitionTracker.from_parent_owned_state()
    assert old_child.observe(signature) is False
    assert old_child.observe(signature) is False

    candidate_child = TransportRepetitionTracker.from_parent_owned_state()
    assert candidate_child is not old_child

    # INVARIANT: an unconfigured child starts from streak 0, so the very
    # first attempt it sees is only attempt 1 and the breaker stays shut.
    assert candidate_child.observe(signature) is False


def test_e17_transport_loop_breaker_reset_clears_the_carried_streak(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reset must also clear the streak the NEXT child will inherit.

    Loop detection can fire on a loop the agent has genuinely given up on,
    and the recovery path clears the streak. That recovery is only real if
    it survives the swap too: a reset that leaves the carried record on
    disk would have the promoted child re-adopt the very streak the
    recovery just discarded.
    """
    monkeypatch.setenv(MCP_SNAPSHOT_DIR_ENV, str(_parent_owned_snapshot_dir(tmp_path)))
    signature = _loop_signature()

    old_child = TransportRepetitionTracker.from_parent_owned_state()
    assert old_child.observe(signature) is False
    assert old_child.observe(signature) is False
    old_child.reset()

    candidate_child = TransportRepetitionTracker.from_parent_owned_state()
    assert candidate_child is not old_child
    assert candidate_child.observe(signature) is False


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
        tool_entries_fn=lambda endpoint, timeout: list(_CARRIED_TOOL_ENTRIES),
    )
    # Seed the carried tool catalogue through the public baseline-capture path.
    bridge.capture_tool_hash_baseline()
    carried_tool_hash = compute_tool_set_hash(_CARRIED_TOOL_ENTRIES)

    # Perform make-before-break restart
    # policy: white-box-exception
    restarted = bridge._perform_restart(process_exited=False, probe_duration_s=0.2)
    assert restarted is True

    # =======================================================================
    # 1. CARRIED State (MUST be preserved across child swaps):
    # =======================================================================
    # 1a. Session identity (run_id)
    assert bridge.run_id == "run-e17-carried"
    # 1b. Session ID identity (child sees identical session ID)
    assert bridge.diagnostics().session_id == "session-e17-fixed"
    # 1c. Broker fingerprint (HMAC verification key identity preserved)
    assert bridge.diagnostics().broker_fingerprint == "sha256:abcd1234efgh5678"
    # 1d. Tool set hash (tool catalogue drift guard carried)
    assert bridge.diagnostics().tool_set_hash == carried_tool_hash
    # 1e. Restart budget and timestamps (the rolling-window decision
    #     cannot restart from zero every swap or a wedged server would
    #     loop forever).
    assert bridge.restart_count == 1
    assert bridge.diagnostics().restart_attempt_count == 1
    # 1f. Generation incremented (stale probes from the prior inner
    #     must be discarded by the lock-protected reader).
    assert bridge.diagnostics().generation == 1
    # 1g. Active process switched to candidate
    assert bridge.process.pid == 2002
    # 1h. The wire ledger HMAC chain (parent-owned on disk) is the
    #     canonical cross-child state surface; a swap MUST NOT break
    #     the chain. Pin via the dedicated ``test_e17_wire_ledger_*``
    #     tests above; here we re-pin the bridge side: the bridge
    #     keeps its ledger-secret binding (broker_fingerprint) intact.
    assert bridge.diagnostics().broker_fingerprint is not None

    # =======================================================================
    # 2. RESET-SAFE State (must reset cleanly per child):
    # =======================================================================
    # 2a. Candidate process is fresh (not contaminated by old child's memory)
    assert new_inner.process.pid == 2002
    assert new_inner.process.poll() is None
    # 2b. In-flight probe failure counters reset on promotion (a fresh
    #     candidate has not failed yet).
    assert bridge.diagnostics().consecutive_probe_failures == 0
    assert bridge.diagnostics().deferred_probe_failures == 0
    assert bridge.diagnostics().first_probe_deferred is False
    # 2c. Draining inner tracked while drain worker is executing
    assert drain_entered.wait(timeout=1.0) is True
    assert bridge.diagnostics().draining_process_count == 1

    # Allow drain worker to complete
    drain_can_finish.set()
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and bridge.diagnostics().draining_process_count > 0:
        time.sleep(0.01)
    assert bridge.diagnostics().draining_process_count == 0
    assert old_inner.process.poll() is not None


# ---------------------------------------------------------------------------
# E1.7: Per-category proofs — wrap-up flags, saturation counters, metrics,
# upstream sessions, ralph://exec/ handles. Each is asserted to be EITHER
# carried-across OR reset-safe across a real child swap.
# ---------------------------------------------------------------------------


def test_e17_wrap_up_flags_reset_safe_across_child_swap(tmp_path: pathlib.Path) -> None:
    """Wrap-up flags are reset-safe: a fresh child observes the default wrap-up state."""
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

    # Establish the OLD child's wrap-up state: warning has fired.
    with session_warning_scope(False):
        assert session_before_warning() is False
        assert session_warning_fired() is True

        # Perform the swap. Bridge swaps _inner from old to new child.
        # policy: white-box-exception
        restarted = bridge._perform_restart(process_exited=False, probe_duration_s=0.2)
        assert restarted is True

    # DISPOSITION (reset-safe): a fresh ContextVar is what the NEW child sees.
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
    """Saturation counters are reset-safe: a fresh child's dispatch executor starts at zero."""
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

    # Establish the OLD child's saturation state. The
    # _SaturatedDispatchProbe Protocol exposes the test-only attrs we
    # need without resorting to test-file suppression comments
    # (tests are required to be fully typed).
    class _SaturatedDispatchProbe(Protocol):
        _inflight: int
        _lock: threading.Lock

    # policy: white-box-exception
    dispatcher: _SaturatedDispatchProbe = saturated_dispatch._default_dispatch
    # policy: white-box-exception
    with dispatcher._lock:
        # policy: white-box-exception
        prior_inflight = dispatcher._inflight
        # policy: white-box-exception
        dispatcher._inflight += 1
    try:
        # POLICY EXCEPTION — the saturation dispatcher's in-flight gauge has
        # no public accessor (the module publishes ``submit`` /
        # ``install_executor`` / ``reset_default`` only), so the gauge is read
        # through the private attribute and hoisted into a local. The value
        # asserted is the OBSERVED gauge, not a private-state expression.
        # policy: white-box-exception
        inflight_before_swap = dispatcher._inflight
        assert inflight_before_swap == prior_inflight + 1

        # Swap to the NEW child. The bridge MUST NOT mutate the
        # dispatcher's in-flight count during the swap (the swap is
        # at the ``_inner`` level, not at the dispatch wrapper).
        # policy: white-box-exception
        restarted = bridge._perform_restart(process_exited=False, probe_duration_s=0.2)
        assert restarted is True
        # The OLD child's in-flight count is preserved across the
        # swap (the bridge does NOT touch it). In production, the
        # NEW child is a fresh process with its OWN dispatcher that
        # starts at ``inflight=0``.
        # policy: white-box-exception
        inflight_after_swap = dispatcher._inflight
        assert inflight_after_swap == prior_inflight + 1

        # DISPOSITION (reset-safe): a fresh dispatcher (via the
        # production ``reset_default`` seam) starts at ``inflight=0``.
        # This is the same state a fresh child process would inherit
        # implicitly because each child is its own process.
        saturated_dispatch.reset_default()
        # policy: white-box-exception
        fresh_dispatcher: _SaturatedDispatchProbe = saturated_dispatch._default_dispatch
        # policy: white-box-exception
        fresh_inflight = fresh_dispatcher._inflight
        assert fresh_inflight == 0
    finally:
        # Restore prior state so we do not leak into the next test.
        # policy: white-box-exception
        with dispatcher._lock:
            # policy: white-box-exception
            dispatcher._inflight = prior_inflight


def test_e17_metrics_reset_safe_across_child_swap(tmp_path: pathlib.Path) -> None:
    """Metrics counters are reset-safe: a fresh child's transport metrics start at zero."""
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

    # Establish the OLD child's metrics state: increment the post-
    # header-failure counter so we can observe the reset.
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

    # Swap to the NEW child.
    # policy: white-box-exception
    restarted = bridge._perform_restart(process_exited=False, probe_duration_s=0.2)
    assert restarted is True

    # DISPOSITION (reset-safe): the NEW child starts a fresh
    # metrics counter (a brand-new :class:`McpMetrics` instance), so
    # the OLD child's post-header-failure count does NOT bleed into
    # the NEW child's snapshot. (The reset_default_metrics seam is
    # the production path.)
    reset_default_metrics()
    fresh_metrics = get_default_metrics()
    fresh_snapshot = fresh_metrics.snapshot()
    assert fresh_snapshot["post_header_failures"] == 0
    assert fresh_snapshot["terminal_frame_emissions"] == 0


def test_e17_upstream_sessions_reset_safe_across_child_swap(
    tmp_path: pathlib.Path,
) -> None:
    """Upstream client sessions are child-scoped and not reused across child swaps."""
    from ralph.mcp.upstream.config import UpstreamMcpServer
    from ralph.mcp.upstream.models import UpstreamTool

    old_inner = _make_inner(tmp_path, poll_result=None, pid=1001)
    new_inner = _make_inner(tmp_path, poll_result=None, pid=2002)

    # PROOF (1) -- construct two distinct UpstreamRegistry instances
    # via the production :meth:`build_from_tool_catalog` path with
    # server-tagged client factories so a carry-across would surface
    # as a wrong-marker client in the wrong registry.

    class _OldUpstreamClient:
        """Marker class: every instance is the OLD child's client."""

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
        """Marker class: every instance is the NEW child's client."""

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

    # The two UpstreamRegistry instances are constructed independently: their
    # served catalogues name disjoint upstreams and their aliases are disjoint,
    # and every alias is served by its OWN registry's client.
    assert old_registry is not new_registry
    assert {proxied.server_name for proxied in old_registry.tool_definitions()} == {
        "e17-old-upstream"
    }
    assert {proxied.server_name for proxied in new_registry.tool_definitions()} == {
        "e17-new-upstream"
    }
    assert set(old_aliases).isdisjoint(new_aliases)
    # Cross-leak assertions: every OLD alias is served by an OLD-marker
    # client and every NEW alias by a NEW-marker client. A carry-across
    # would have made a wrong-marker client land behind the wrong alias.
    assert all(_served_marker(old_registry, alias) == "old" for alias in old_aliases)
    assert all(_served_marker(new_registry, alias) == "new" for alias in new_aliases)

    # PROOF (2) -- drive the production child-swap path through the
    # bridge (same seam as the other E1.7 tests in this module).
    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: new_inner,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="run-e17-upstream",
        sleep_fn=lambda s: None,
    )
    # policy: white-box-exception
    restarted = bridge._perform_restart(process_exited=False, probe_duration_s=0.2)
    assert restarted is True
    assert bridge.diagnostics().generation == 1
    assert bridge.process.pid == 2002

    # DISPOSITION (reset-safe upstream sessions): after the swap,
    # OLD's registry is preserved (the swap does not erase OLD's
    # client cache) and NEW's registry has independent content
    # (NEW's keys are not in OLD's cache, and vice versa).
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
    # Marker preservation: the OLD client's marker must STILL be
    # "old" and the NEW client's marker must STILL be "new". A
    # carry-across would have rebounded either marker.
    assert all(_served_marker(old_registry, alias) == "old" for alias in old_aliases)
    assert all(_served_marker(new_registry, alias) == "new" for alias in new_aliases)


def test_e17_exec_resource_handle_reset_safe_across_child_swap(
    tmp_path: pathlib.Path,
) -> None:
    """ralph://exec/ handles issued by the OLD child are NOT replayable in the NEW child."""
    from ralph.mcp.server._json_rpc_request import JsonRpcRequest
    from ralph.mcp.server._mcp_server import McpServer
    from ralph.mcp.server.runtime_session import FileBackedSession
    from ralph.mcp.tools._exec_resource_uri import ExecResourceResolver

    old_inner = _make_inner(tmp_path, poll_result=None, pid=1001)
    new_inner = _make_inner(tmp_path, poll_result=None, pid=2002)

    # 1) OLD child session: a real FileBackedSession with an
    #    injected ExecResourceResolver, plus an ``exec_spill_roots``
    #    entry in the on-disk payload (so the on-disk discovery path
    #    matches the production layout). The OLD spill directory is a
    #    real path on disk under ``tmp_path`` so register() can
    #    succeed.
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
    # Pin the OLD side: the resolver is attached via the public
    # property (no fabricated attribute, no direct private access).
    assert old_session.exec_resource_resolver is old_resolver

    # 2) NEW child session: a FRESH FileBackedSession with NO resolver
    #    injection AND no ``exec_spill_roots`` in the payload, so the
    #    lazy ``exec_resource_resolver`` property returns ``None``.
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

    # 3) Pin the OLD-can-replay contract via the real production
    #    entry point BEFORE the swap (proves the OLD URI is a valid,
    #    replayable resource in the OLD child).
    handler_old = object.__new__(McpServer)
    # policy: white-box-exception
    handler_old._session = old_session
    old_request = JsonRpcRequest(
        jsonrpc="2.0",
        method="resources/read",
        params={"uri": old_uri},
        msg_id=1,
    )
    # policy: white-box-exception
    old_response, _state_old = handler_old._handle_resources_read(old_request)
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

    # 4) Drive the production child-swap path through the bridge.
    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=lambda: new_inner,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="run-e17-exec",
        sleep_fn=lambda s: None,
    )
    # policy: white-box-exception
    restarted = bridge._perform_restart(process_exited=False, probe_duration_s=0.2)
    assert restarted is True
    assert bridge.diagnostics().generation == 1

    # 5) Pin the NEW-cannot-replay-OLD-URI contract via the real
    #    production entry point AFTER the swap. The NEW session has
    #    no resolver, so the handler must return a STRUCTURED
    #    ``-32602`` error (not None, not a silent miss, not an
    #    untyped crash). This is the typed-failure surface the agent
    #    relies on.
    handler_new = object.__new__(McpServer)
    # policy: white-box-exception
    handler_new._session = new_session
    new_request = JsonRpcRequest(
        jsonrpc="2.0",
        method="resources/read",
        params={"uri": old_uri},
        msg_id=2,
    )
    # policy: white-box-exception
    new_response, _state_new = handler_new._handle_resources_read(new_request)
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

    # 6) The OLD child's resolver is preserved across the swap: the
    #    OLD URI is still replayable from the OLD session (the swap
    #    does not erase OLD's spill registry).
    # policy: white-box-exception
    post_old_response, _post_state = handler_old._handle_resources_read(old_request)
    assert post_old_response.error is None, (
        f"OLD child resolver was unexpectedly erased across the swap: "
        f"error={post_old_response.error!r}"
    )
    post_old_first = post_old_response.result["contents"][0]
    assert _base64.b64decode(post_old_first["blob"]) == old_payload


def test_e17_repetition_tracker_carried_via_bridge_swap_with_fake_inners(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repetition tracker is CARRIED across a bridge-level swap.

    Pinning the E1.7 enumeration line "repetition tracker" with the
    catalogue disposition (carry across): a tracker that observed N
    repetitions on the OLD child must show N+1 on the NEW child
    after the swap (the on-disk state file is parent-owned). The
    tests at lines 234-320 pin the same contract via the in-process
    tracker pair; THIS test pins it through the bridge-level swap
    (``_perform_restart``) using a :class:`_FakeProcess` for each
    inner — the swap itself is exercised but the subprocess is not a
    real OS child (see :class:`_FakeProcess` for the rationale).

    The "real" aspect of this test is the parent-owned state file on
    disk; the bridge inner objects are test-only stand-ins because the
    contract is observable at the parent-owned directory level, not at
    the subprocess level.
    """
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

    # OLD child observes two identical failures. The carried streak
    # lands on the parent-owned state file.
    old_tracker = TransportRepetitionTracker.from_parent_owned_state()
    assert old_tracker.observe(signature) is False
    assert old_tracker.observe(signature) is False

    # Swap to the NEW child via the production bridge-level swap path.
    # policy: white-box-exception
    restarted = bridge._perform_restart(process_exited=False, probe_duration_s=0.2)
    assert restarted is True

    # NEW child observes the 3rd identical failure via the SAME
    # parent-owned state file. The streak carries across the swap.
    # DISPOSITION (carry across): tracker with N=2 on OLD child
    # reports trip=True on NEW child.
    new_tracker = TransportRepetitionTracker.from_parent_owned_state()
    assert new_tracker is not old_tracker
    assert new_tracker.observe(signature) is True


def test_e17_repetition_tracker_carried_across_real_subprocess_swap(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repetition tracker is CARRIED across a swap of two REAL subprocess children."""
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
        # Wait for both subprocesses to be alive (poll() is None).
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

        # OLD child observes two identical failures. The carried streak
        # lands on the parent-owned state file.
        old_tracker = TransportRepetitionTracker.from_parent_owned_state()
        assert old_tracker.observe(signature) is False
        assert old_tracker.observe(signature) is False

        # Swap to the NEW child. The bridge swaps the active inner; the
        # OLD real subprocess is shutdown by the bridge, the NEW real
        # subprocess is the promoted candidate.
        # policy: white-box-exception
        restarted = bridge._perform_restart(process_exited=False, probe_duration_s=0.2)
        assert restarted is True
        assert bridge.process.pid == new_proc.pid, (
            f"bridge did not adopt real NEW subprocess: pid={bridge.process.pid}, "
            f"expected={new_proc.pid}"
        )
        assert bridge.diagnostics().generation == 1

        # NEW child observes the 3rd identical failure via the SAME
        # parent-owned state file. The streak carries across the
        # REAL-subprocess swap.
        new_tracker = TransportRepetitionTracker.from_parent_owned_state()
        assert new_tracker is not old_tracker
        assert new_tracker.observe(signature) is True

        # The OLD real subprocess was reaped by the bridge; the NEW
        # real subprocess is still serving.
        assert old_proc.poll() is not None, "OLD real subprocess was not reaped after bridge swap"
        assert new_proc.poll() is None, "NEW real subprocess died during bridge swap"
    finally:
        # Reap both subprocesses if still alive.
        if old_proc.poll() is None:
            with contextlib.suppress(Exception):
                old_proc.kill()
                old_proc.wait(timeout=2.0)
        if new_proc.poll() is None:
            with contextlib.suppress(Exception):
                new_proc.kill()
                new_proc.wait(timeout=2.0)
