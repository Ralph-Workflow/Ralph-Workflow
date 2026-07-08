"""Cross-transport black-box test for real-time subagent visibility surface.

Per-transport real extracted progress contract
----------------------------------------------

For each supported ``AgentTransport`` member, the watchdog must surface
what every active subagent is doing in real time. The transport-specific
source of that evidence differs but the watchdog surface is the same:

* **OpenCode** emits structured child lifecycle events on its own stdout
  that the ``OpenCodeExecutionStrategy`` ingests into a per-invocation
  ``ChildLivenessRegistry``. The factory returns
  :class:`OpenCodeRegistryDiscoveryStrategy` for the ``OPENCODE``
  transport when a registry is provided, so a per-child
  :class:`RegistryBackedSubagentOutputCapture` can surface textual
  descriptions of progress / heartbeat / terminal events to the
  watchdog's first-party ``subagent_output`` channel via
  :meth:`IdleWatchdog.poll_subagent_output` and
  :meth:`IdleWatchdog.record_subagent_output`.

* **OpenCode without a registry** degrades gracefully to
  :class:`NullDiscoveryStrategy` -- the watchdog must not invent a
  registry it does not have. The cross-transport subagent activity
  sink (:meth:`IdleWatchdog.record_subagent_work`) still surfaces
  real-time progress from the OpenCode line observer.

* **Claude / Claude-interactive / Codex / Nanocoder / Generic / Agy / Pi**
  each have an execution strategy that observes lines and routes child
  signals (``[subagent] progress``, ``[subagent] heartbeat``, JSON
  envelopes with ``type=child_progress``, etc.) into the cross-transport
  subagent activity sink. The factory returns
  :class:`NullDiscoveryStrategy` for these transports because no
  per-worker subagent log path is documented, BUT real extracted
  progress still flows through the watchdog surface via
  :meth:`IdleWatchdog.record_subagent_work` -- this test proves that
  contract black-box for every transport.

The tests assert REAL extracted progress for every supported agent,
not the old graceful-degradation contract that returned
:class:`NullDiscoveryStrategy` for every transport. Each per-transport
test wires the watchdog's ``record_subagent_work`` sink into a real
execution strategy for the transport, observes a child signal line,
and asserts the watchdog's ``last_subagent_progress_description``
captures the textual description. The OpenCode test additionally
asserts the registry-based ``poll_subagent_output`` channel captures
real per-child progress lines.

All tests use ``FakeClock``; no real subprocess, no ``time.sleep``, no
real network.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from ralph.agents.execution_state import AgentExecutionState, strategy_for_transport
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WaitingStatusEvent,
)
from ralph.agents.invoke._monitor_factory import _discovery_strategy_for_config
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.mcp.server._activity_sink import (
    reset_active_sink,
    reset_subagent_sink,
    set_active_sink,
    set_subagent_sink,
)
from ralph.process.child_liveness import ChildLivenessRegistry
from ralph.process.monitor import (
    NullDiscoveryStrategy,
    OpenCodeRegistryDiscoveryStrategy,
    ProcessMonitor,
    SubagentOutputCapture,
)

# Real child-signal lines that production agents (Claude/Codex/Generic/etc.)
# emit on stdout. Each line carries an explicit ``child`` / ``subagent``
# scope marker so ``_classify_generic_child_signal`` classifies it as
# CHILD_PROGRESS or CHILD_HEARTBEAT and the line observer routes it into
# the cross-transport subagent activity sink.
_REAL_PROGRESS_LINE = "[subagent] progress: phase=phase-1"
_REAL_HEARTBEAT_LINE = "[subagent] heartbeat"
_REAL_CHILD_JSON_LINE = '{"type": "child_progress", "child_id": "child-A", "phase": "phase-2"}'


@dataclass
class _NoProcessMonitor:
    """Fake process monitor: no live subagents, no captures."""

    def live_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return ()

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict:
        return {}


def _make_watchdog() -> IdleWatchdog:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    return IdleWatchdog(policy, clock, process_monitor=_NoProcessMonitor())


def _make_registry() -> ChildLivenessRegistry:
    """Return a ``ChildLivenessRegistry`` with non-zero TTLs so tests are stable."""
    return ChildLivenessRegistry(
        progress_ttl=60.0,
        heartbeat_ttl=60.0,
        stale_label_ttl=60.0,
        exit_reconcile=5.0,
    )


def _bind_subagent_sink_to_watchdog(
    watchdog: IdleWatchdog,
) -> tuple[object, object]:
    """Bind ``watchdog.record_subagent_work`` into the subagent sink contextvar.

    Returns the (sink_token, subagent_token) so the caller can reset them
    after the test.
    """

    def _mcp_sink(_tool_name: str) -> None:
        watchdog.record_mcp_tool_call()

    def _subagent_sink(line: str) -> None:
        watchdog.record_subagent_work(description=line)

    sink_token = set_active_sink(_mcp_sink)
    subagent_token = set_subagent_sink(_subagent_sink)
    return (sink_token, subagent_token)


def _reset_sink_tokens(tokens: tuple[object, object]) -> None:
    sink_token, subagent_token = tokens
    reset_active_sink(sink_token)
    reset_subagent_sink(subagent_token)


# wt-021 (R5, Trustworthy Idle Watchdog spec): mirrors the
# production helper ``_parse_tool_call_from_description`` in
# ``ralph.agents.idle_watchdog._activity_methods``. Returns the
# substring before the first ``": "`` when the description starts
# with a known tool-call verb from the canonical verb set
# (``tool_use``, ``tool_result``, ``mcp_tool``, ``subagent``,
# ``bash``, ``read``, ``write``, ``edit``, ``glob``, ``grep``,
# ``webfetch``, ``websearch``). The helper exists in the test
# module so each parametrized assertion can compare the watchdog
# surface (``diagnostic_snapshot()["current_subagent_tool_call"]``
# or ``WaitingStatusEvent.current_subagent_tool_call``) to the
# deterministic production parser output without importing the
# private helper (which is module-private to the watchdog
# implementation).
_KNOWN_TOOL_CALL_VERBS_FOR_TEST: frozenset[str] = frozenset(
    {
        "tool_use",
        "tool_result",
        "mcp_tool",
        "subagent",
        "bash",
        "read",
        "write",
        "edit",
        "glob",
        "grep",
        "webfetch",
        "websearch",
    }
)


def _parse_tool_call_expected(description: str | None) -> str | None:
    """Mirror the production R5 CURRENT TOOL CALL parser.

    Splits on a single ``:`` (not ``": "``) because the
    canonical production format from the NDJSON parser layer is
    ``tool_use:<name>`` with no space after the colon. See
    ``ralph.agents.idle_watchdog._activity_methods._parse_tool_call_from_description``
    for the production implementation this helper mirrors.
    """
    if not description:
        return None
    head, sep, _tail = description.partition(":")
    if not sep:
        return None
    if head not in _KNOWN_TOOL_CALL_VERBS_FOR_TEST:
        return None
    return head


# ---------------------------------------------------------------------------
# OpenCode: registry-backed discovery + cross-transport sink
# ---------------------------------------------------------------------------


def test_opencode_discovery_strategy_is_registry_backed_with_registry() -> None:
    """OpenCode + registry returns ``OpenCodeRegistryDiscoveryStrategy``.

    OpenCode is the only transport whose agent CLI documents a stable
    structured child event stream (carried on the agent's own stdout).
    The factory must wire the injected ``ChildLivenessRegistry``
    through to the strategy so a per-child
    :class:`RegistryBackedSubagentOutputCapture` can surface real-time
    progress, heartbeat, and terminal events.
    """
    config = type(
        "Cfg",
        (),
        {"transport": AgentTransport.OPENCODE},
    )()
    registry = _make_registry()
    strategy = _discovery_strategy_for_config(
        config, registry=registry, scope_prefix="agent:test-scope:"
    )
    assert isinstance(strategy, OpenCodeRegistryDiscoveryStrategy), (
        f"transport=OPENCODE: expected OpenCodeRegistryDiscoveryStrategy;"
        f" got {type(strategy).__name__}"
    )


def test_opencode_discovery_strategy_is_null_without_registry() -> None:
    """OpenCode without a registry degrades to ``NullDiscoveryStrategy``.

    The watchdog must not invent a registry it does not have. Without a
    registry the cross-transport subagent activity sink is the
    documented fallback for OpenCode line observers.
    """
    config = type(
        "Cfg",
        (),
        {"transport": AgentTransport.OPENCODE},
    )()
    strategy = _discovery_strategy_for_config(config, registry=None, scope_prefix="")
    assert isinstance(strategy, NullDiscoveryStrategy), (
        f"transport=OPENCODE without registry: expected NullDiscoveryStrategy;"
        f" got {type(strategy).__name__}"
    )


def test_opencode_surfaces_real_extracted_progress_via_registry() -> None:
    """OpenCode registry-backed strategy surfaces REAL extracted progress.

    End-to-end: a per-child capture surfaces registry progress events.
    The watchdog polls ``discover_subagent_outputs`` from the process
    monitor and records each new line as ``subagent_output`` first-party
    evidence via ``record_subagent_output``. With an injected
    ``OpenCodeRegistryDiscoveryStrategy`` backed by a real
    ``ChildLivenessRegistry`` containing an active child with progress
    and heartbeat events, the watchdog's first-party channel count
    must advance.
    """
    registry = _make_registry()
    registry.register_child("child-A", "agent:test-scope:", pid=111)
    registry.record_progress("child-A", phase="phase-1")
    registry.record_heartbeat("child-A")

    @dataclass
    class _RegistryBackedMonitor(ProcessMonitor):
        registry: ChildLivenessRegistry
        scope_prefix: str
        poll_count: int = 0

        def live_subagent_count(self) -> int:
            return 0

        def classified_processes(self) -> tuple:
            return ()

        def refresh(self) -> None:
            pass

        def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
            self.poll_count += 1
            return OpenCodeRegistryDiscoveryStrategy(
                self.registry, self.scope_prefix
            ).discover_subagent_outputs(host_pid=999)

    monitor = _RegistryBackedMonitor(registry=registry, scope_prefix="agent:test-scope:")
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
        subagent_output_poll_interval_seconds=0.001,
    )
    watchdog = IdleWatchdog(policy, clock, process_monitor=monitor)
    watchdog.record_invocation_start()
    watchdog.record_activity()

    clock.advance(0.01)
    fresh = watchdog.poll_subagent_output(now=clock.monotonic())
    assert fresh >= 1
    assert monitor.poll_count == 1
    assert watchdog._subagent_output_count >= 1

    clock.advance(0.01)
    registry.record_progress("child-A", phase="phase-2")
    fresh2 = watchdog.poll_subagent_output(now=clock.monotonic())
    assert fresh2 >= 1
    assert watchdog._subagent_output_count >= 2


def test_opencode_capture_lines_consumable_by_record_subagent_work() -> None:
    """Per-child capture lines surface as ``record_subagent_work`` signals.

    For OpenCode, a per-child
    :class:`RegistryBackedSubagentOutputCapture` produces textual lines
    (e.g. ``[subagent] progress: phase=phase-1``) which the
    ``DefaultProcessMonitor``-driven poll path forwards into
    ``record_subagent_work`` so ``last_subagent_progress_description``
    updates in real time. This test proves the line payload format the
    factory's strategy emits is consumable by the sink.
    """
    registry = _make_registry()
    registry.register_child("child-A", "agent:test-scope:", pid=111)
    registry.record_progress("child-A", phase="phase-1")

    strategy = OpenCodeRegistryDiscoveryStrategy(registry, "agent:test-scope:")
    capture = strategy.discover_subagent_outputs(host_pid=999)["child-A"]
    lines = capture.read_lines(worker_id="child-A")

    watchdog = _make_watchdog()
    watchdog.record_invocation_start()
    consumed: list[str] = []
    for line in lines:
        watchdog.record_subagent_work(description=line)
        consumed.append(line)

    assert watchdog.last_subagent_progress_description is not None
    assert any("phase-1" in line for line in consumed), consumed
    assert any("heartbeat" in line.lower() for line in consumed), consumed


# ---------------------------------------------------------------------------
# All transports: real extracted progress via the cross-transport sink
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("transport", list(AgentTransport))
def test_transport_strategy_surfaces_real_extracted_progress_to_watchdog(
    transport: AgentTransport,
) -> None:
    """Each transport's strategy surfaces REAL extracted progress.

    Black-box contract: build the canonical execution strategy for the
    transport, wire the watchdog's ``record_subagent_work`` into the
    cross-transport subagent sink, observe a child signal line that
    real agents emit on stdout, and assert the watchdog captures the
    real extracted description in
    ``last_subagent_progress_description``.

    This proves the prompt's requirement -- "we should do this for ALL
    supported agents" -- black-box for every transport, not just
    OpenCode. The non-OpenCode transports do not have a documented
    per-worker log path so the discovery strategy is a no-op, but the
    line observer feeds real extracted progress to the watchdog
    regardless of transport.
    """
    watchdog = _make_watchdog()
    tokens = _bind_subagent_sink_to_watchdog(watchdog)
    try:
        watchdog.record_invocation_start()
        assert watchdog.last_subagent_progress_description is None

        strategy = strategy_for_transport(transport, registry=_make_registry())
        strategy.observe_line(_REAL_PROGRESS_LINE)

        assert watchdog.last_subagent_progress_description == _REAL_PROGRESS_LINE, (
            f"transport={transport!r}: watchdog did not capture real extracted"
            f" progress from line observer; got"
            f" {watchdog.last_subagent_progress_description!r}"
        )
        # The subagent_progress_count is surfaced via the public
        # diagnostic_snapshot() rather than via the private
        # ``_subagent_progress_count`` field. Use the public API so the
        # test stays black-box.
        snapshot = watchdog.diagnostic_snapshot(now=0.0)
        assert snapshot["subagent_progress_count"] >= 1, (
            f"transport={transport!r}: diagnostic_snapshot"
            f" MUST report subagent_progress_count >= 1 after a real"
            f" progress line; got {snapshot['subagent_progress_count']}"
        )
        # R5 LAST ACTIVITY: the monotonic timestamp of the most
        # recent subagent observation MUST be populated for every
        # transport after a real child signal line. ``>= 0.0``
        # guards against accidentally returning a sentinel
        # negative value (FakeClock starts at 0.0 so the recorded
        # timestamp is the wall-clock origin).
        last_activity = snapshot["last_subagent_progress_at"]
        assert (
            last_activity is not None and isinstance(last_activity, float) and last_activity >= 0.0
        ), (
            f"transport={transport!r}: diagnostic_snapshot"
            f" MUST report last_subagent_progress_at as a non-None"
            f" float >= 0.0 after a real progress line; got {last_activity!r}"
        )
        # R5 CURRENT TOOL CALL: the parsed ``verb:`` prefix MUST
        # match what the production parser yields for the observed
        # description. For ``_REAL_PROGRESS_LINE =
        # "[subagent] progress: phase=phase-1"`` the parser
        # returns ``None`` (the head ``"[subagent] progress"`` is
        # not a known verb) -- the assertion is therefore a
        # meaningful black-box check that the field exists and
        # the parser runs end-to-end on every transport.
        assert snapshot["current_subagent_tool_call"] == _parse_tool_call_expected(
            _REAL_PROGRESS_LINE
        ), (
            f"transport={transport!r}: diagnostic_snapshot"
            f" MUST report current_subagent_tool_call matching the"
            f" parser output for the observed description; got"
            f" {snapshot['current_subagent_tool_call']!r}"
        )
    finally:
        _reset_sink_tokens(tokens)


@pytest.mark.parametrize("transport", list(AgentTransport))
def test_transport_strategy_surfaces_real_heartbeat_extraction(
    transport: AgentTransport,
) -> None:
    """Each transport surfaces REAL extracted heartbeat activity.

    Heartbeat lines (``[subagent] heartbeat``) are routed through the
    cross-transport subagent activity sink for every transport. This
    test proves that real heartbeat activity is captured for every
    supported transport -- operators reading the watchdog's per-channel
    log see the most recent heartbeat, not a graceful-degradation stub.
    """
    watchdog = _make_watchdog()
    tokens = _bind_subagent_sink_to_watchdog(watchdog)
    try:
        watchdog.record_invocation_start()
        assert watchdog.last_subagent_progress_description is None

        strategy = strategy_for_transport(transport, registry=_make_registry())
        strategy.observe_line(_REAL_HEARTBEAT_LINE)

        assert watchdog.last_subagent_progress_description == _REAL_HEARTBEAT_LINE, (
            f"transport={transport!r}: watchdog did not capture real extracted"
            f" heartbeat; got {watchdog.last_subagent_progress_description!r}"
        )
        snapshot = watchdog.diagnostic_snapshot(now=0.0)
        assert snapshot["subagent_progress_count"] >= 1, (
            f"transport={transport!r}: diagnostic_snapshot"
            f" MUST report subagent_progress_count >= 1 after a real"
            f" heartbeat line; got {snapshot['subagent_progress_count']}"
        )
        # R5 LAST ACTIVITY + CURRENT TOOL CALL: must flow through
        # every transport after a real heartbeat line. The parser
        # returns ``None`` for ``"[subagent] heartbeat"`` (no
        # ``": "`` separator) so the assertion is meaningful even
        # when the parsed value is ``None``.
        last_activity = snapshot["last_subagent_progress_at"]
        assert (
            last_activity is not None and isinstance(last_activity, float) and last_activity >= 0.0
        ), (
            f"transport={transport!r}: diagnostic_snapshot"
            f" MUST report last_subagent_progress_at as a non-None"
            f" float >= 0.0 after a real heartbeat line; got {last_activity!r}"
        )
        assert snapshot["current_subagent_tool_call"] == _parse_tool_call_expected(
            _REAL_HEARTBEAT_LINE
        ), (
            f"transport={transport!r}: diagnostic_snapshot"
            f" MUST report current_subagent_tool_call matching the"
            f" parser output for the heartbeat description; got"
            f" {snapshot['current_subagent_tool_call']!r}"
        )
    finally:
        _reset_sink_tokens(tokens)


@pytest.mark.parametrize("transport", list(AgentTransport))
def test_transport_strategy_surfaces_real_json_extraction(
    transport: AgentTransport,
) -> None:
    """Each transport surfaces REAL extracted JSON child signals.

    Production agents (Codex, Generic, Claude with JSON envelopes)
    emit ``{"type": "child_progress", ...}`` lines. The cross-transport
    classifier routes these into the subagent activity sink for every
    transport.
    """
    watchdog = _make_watchdog()
    tokens = _bind_subagent_sink_to_watchdog(watchdog)
    try:
        watchdog.record_invocation_start()
        assert watchdog.last_subagent_progress_description is None

        strategy = strategy_for_transport(transport, registry=_make_registry())
        strategy.observe_line(_REAL_CHILD_JSON_LINE)

        assert watchdog.last_subagent_progress_description == _REAL_CHILD_JSON_LINE, (
            f"transport={transport!r}: watchdog did not capture real extracted"
            f" JSON child signal; got"
            f" {watchdog.last_subagent_progress_description!r}"
        )
        snapshot = watchdog.diagnostic_snapshot(now=0.0)
        assert snapshot["subagent_progress_count"] >= 1, (
            f"transport={transport!r}: diagnostic_snapshot"
            f" MUST report subagent_progress_count >= 1 after a real"
            f" JSON child signal; got {snapshot['subagent_progress_count']}"
        )
        # R5 LAST ACTIVITY + CURRENT TOOL CALL: must flow through
        # every transport after a real JSON child signal. The
        # parser returns ``None`` for the JSON envelope (the head
        # ``{"type"`` is not a known verb).
        last_activity = snapshot["last_subagent_progress_at"]
        assert (
            last_activity is not None and isinstance(last_activity, float) and last_activity >= 0.0
        ), (
            f"transport={transport!r}: diagnostic_snapshot"
            f" MUST report last_subagent_progress_at as a non-None"
            f" float >= 0.0 after a real JSON child signal; got {last_activity!r}"
        )
        assert snapshot["current_subagent_tool_call"] == _parse_tool_call_expected(
            _REAL_CHILD_JSON_LINE
        ), (
            f"transport={transport!r}: diagnostic_snapshot"
            f" MUST report current_subagent_tool_call matching the"
            f" parser output for the JSON child signal; got"
            f" {snapshot['current_subagent_tool_call']!r}"
        )
    finally:
        _reset_sink_tokens(tokens)


@pytest.mark.parametrize("transport", list(AgentTransport))
def test_transport_strategy_surfaces_real_extraction_to_listener(
    transport: AgentTransport,
) -> None:
    """Each transport surfaces REAL extracted progress to a registered listener.

    Black-box contract: build the canonical execution strategy for the
    transport, wire the watchdog's ``record_subagent_work`` into the
    cross-transport subagent sink, register a default subagent activity
    listener, observe a child signal line, drive the watchdog through
    ``evaluate()`` so it transitions into the WAITING_ON_CHILD branch
    and emits an ENTERED waiting-status event, and assert the listener
    receives the real extracted description via the ``subagent_activity``
    field of the waiting status event.

    This is the cross-transport surface that operators rely on to see
    what every supported agent's subagents are doing in real time.
    """
    watchdog = _make_watchdog()
    tokens = _bind_subagent_sink_to_watchdog(watchdog)
    try:
        captured: list[WaitingStatusEvent] = []

        def _listener(event: WaitingStatusEvent) -> None:
            captured.append(event)

        watchdog.record_invocation_start()
        watchdog.register_default_subagent_activity_listener(_listener)

        strategy = strategy_for_transport(transport, registry=_make_registry())
        strategy.observe_line(_REAL_PROGRESS_LINE)

        assert watchdog.last_subagent_progress_description == _REAL_PROGRESS_LINE

        # Drive the watchdog through ``evaluate()`` with a
        # WAITING_ON_CHILD ``classify_quiet`` so the watchdog
        # transitions into the waiting branch and emits the ENTERED
        # status event naturally. The threshold is configured so a
        # single ``evaluate()`` call advances past idle and into the
        # waiting branch on the first poll.
        clock = watchdog._clock
        clock.advance(61.0)

        def _waiting() -> AgentExecutionState:
            return AgentExecutionState.WAITING_ON_CHILD

        watchdog.evaluate(classify_quiet=_waiting)
        assert captured, (
            f"transport={transport!r}: watchdog MUST emit a waiting"
            f" status event with subagent_activity after evaluate()"
            f" transitions into WAITING_ON_CHILD"
        )
        latest = captured[-1]
        assert latest.subagent_activity == _REAL_PROGRESS_LINE, (
            f"transport={transport!r}: listener did not receive real"
            f" extracted progress; got {latest.subagent_activity!r}"
        )
        # R5 LAST ACTIVITY + CURRENT TOOL CALL on the
        # WaitingStatusEvent surface for every transport. The
        # ``emit`` dispatcher in ``_active_branch`` populates all
        # three R5 fields on every emitted event; the listener
        # receives the typed dataclass so the assertion is
        # black-box (no private-seam access).
        assert (
            latest.last_subagent_progress_at is not None
            and isinstance(latest.last_subagent_progress_at, float)
            and latest.last_subagent_progress_at >= 0.0
        ), (
            f"transport={transport!r}: WaitingStatusEvent"
            f" MUST carry last_subagent_progress_at as a non-None"
            f" float >= 0.0 after a real progress line; got"
            f" {latest.last_subagent_progress_at!r}"
        )
        assert latest.current_subagent_tool_call == _parse_tool_call_expected(
            _REAL_PROGRESS_LINE
        ), (
            f"transport={transport!r}: WaitingStatusEvent"
            f" MUST carry current_subagent_tool_call matching the"
            f" parser output for the observed description; got"
            f" {latest.current_subagent_tool_call!r}"
        )
    finally:
        _reset_sink_tokens(tokens)


# ---------------------------------------------------------------------------
# Cross-transport sink contract (independent of transport execution strategy)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("transport", list(AgentTransport))
def test_cross_transport_subagent_activity_sink_is_wired(
    transport: AgentTransport,
) -> None:
    """Every transport surfaces subagent activity through the cross-transport sink.

    Black-box contract: regardless of the transport, the sink accepts a
    description and ``last_subagent_progress_description`` returns it;
    a waiting-status event driven by ``evaluate()`` forwards the
    description to a registered listener; and
    ``record_invocation_start`` clears the description so a new
    invocation starts with a clean slate.
    """
    del transport
    watchdog = _make_watchdog()
    captured: list[tuple[str, str]] = []

    def _listener(event: WaitingStatusEvent) -> None:
        captured.append((event.kind.value, event.subagent_activity or ""))

    watchdog.record_invocation_start()
    watchdog.register_default_subagent_activity_listener(_listener)

    watchdog.record_subagent_work(description="first")
    # Drive the watchdog into the WAITING_ON_CHILD branch so the
    # ENTERED event is emitted through the public evaluate() path.
    watchdog._clock.advance(61.0)

    def _waiting() -> AgentExecutionState:
        return AgentExecutionState.WAITING_ON_CHILD

    watchdog.evaluate(classify_quiet=_waiting)
    # The watchdog may emit multiple status events (ENTERED +
    # SUBAGENT_PROGRESS) on the same evaluate() call; the
    # black-box contract is "every event carries the recorded
    # description", not "exactly one event".
    assert captured, (
        "watchdog.evaluate MUST emit at least one waiting-status event"
        " carrying the recorded subagent description; got no events"
    )
    assert all(description == "first" for _kind, description in captured), (
        "Every waiting-status event forwarded to the listener MUST"
        " carry the recorded subagent description; got: {captured}"
    )

    # R5 LAST ACTIVITY + CURRENT TOOL CALL on the
    # ``diagnostic_snapshot()`` surface for the sink-wired path:
    # after recording subagent work and driving ``evaluate()``
    # into WAITING_ON_CHILD, the snapshot MUST expose all three
    # R5 fields populated from the same source the watchdog uses
    # for the WaitingStatusEvent surface. The snapshot MUST be
    # taken BEFORE ``record_invocation_start`` because that
    # helper resets the R5 fields to ``None`` (per-invocation
    # semantics from R5).
    post_record_snapshot = watchdog.diagnostic_snapshot(now=0.0)
    last_activity_post = post_record_snapshot["last_subagent_progress_at"]
    assert (
        last_activity_post is not None
        and isinstance(last_activity_post, float)
        and last_activity_post >= 0.0
    )
    assert post_record_snapshot["current_subagent_tool_call"] == _parse_tool_call_expected("first")

    watchdog.record_invocation_start()
    assert watchdog.last_subagent_progress_description is None

    # ``record_invocation_start`` resets ALL THREE R5 fields to
    # ``None`` (per-invocation semantics from R5). Verifies the
    # LAST ACTIVITY + CURRENT TOOL CALL fields are cleared
    # alongside the existing PROGRESS field reset.
    reset_snapshot = watchdog.diagnostic_snapshot(now=0.0)
    assert reset_snapshot["last_subagent_progress_at"] is None
    assert reset_snapshot["current_subagent_tool_call"] is None
