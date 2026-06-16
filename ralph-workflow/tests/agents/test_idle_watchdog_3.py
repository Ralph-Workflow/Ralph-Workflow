"""Black-box tests for the activity-aware idle watchdog.

These tests prove the per-channel evidence model:
  - MCP tool calls, subagent work, and workspace events are recorded as
    activity on separate channels.
  - The NO_OUTPUT_DEADLINE fire is DEFERRED while any non-stdout channel
    is fresher than ``activity_evidence_ttl_seconds`` (default 30.0s).
  - Absolute ceilings (SESSION_CEILING_EXCEEDED, CHILDREN_PERSIST_TOO_LONG)
    are unaffected by the per-channel evidence.
  - Truly idle sessions fire at the regular idle deadline.
  - Every watchdog fire embeds the per-channel evidence summary in its
    diagnostic so post-mortems can see WHY the watchdog fired.
  - ``activity_evidence_ttl_seconds=0.0`` disables the feature and
    restores the legacy stdout-only behavior.
  - WorkspaceMonitor publishes ``last_event_at`` via an injectable
    clock so tests can drive timestamps deterministically.

Each test uses ``FakeClock`` and a fresh ``IdleWatchdog``; no real
sleep, no real subprocess, no real I/O. Total wall-clock for the file
is well under 2s.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger as loguru_logger

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    ChannelEvidenceSummary,
    CorroborationSnapshot,
    IdleWatchdog,
    TimeoutPolicy,
    WaitingStatusEvent,
    WaitingStatusKind,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.idle_watchdog._evidence_tier import ChannelName, EvidenceSummary
from ralph.agents.idle_watchdog._workspace_change_kind import WorkspaceChangeKind
from ralph.agents.invoke._workspace import WorkspaceMonitor
from ralph.agents.invoke._workspace_change_classifier import (
    DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS,
    WorkspaceChangeClassifier,
)
from ralph.agents.timeout_clock import FakeClock

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


_IDLE_TIMEOUT = 0.1
_DRAIN_WINDOW = 0.0
_MAX_WAITING = 10.0
_ACTIVITY_TTL = 30.0


def _make_watchdog(
    *,
    idle_timeout: float = _IDLE_TIMEOUT,
    drain_window: float = _DRAIN_WINDOW,
    max_waiting: float = _MAX_WAITING,
    max_session: float | None = None,
    activity_ttl: float | None = _ACTIVITY_TTL,
    start: float = 0.0,
    suspect: float | None = None,
    no_progress_ceiling: float | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    kwargs: dict[str, Any] = {
        "idle_timeout_seconds": idle_timeout,
        "drain_window_seconds": drain_window,
        "max_waiting_on_child_seconds": max_waiting,
        "max_session_seconds": max_session,
        "suspect_waiting_on_child_seconds": suspect,
        "max_waiting_on_child_no_progress_seconds": no_progress_ceiling,
        "os_descendant_only_ceiling_seconds": None,
    }
    if activity_ttl is not None:
        kwargs["activity_evidence_ttl_seconds"] = activity_ttl
    config = TimeoutPolicy(**kwargs)
    clock = FakeClock(start=start)
    return IdleWatchdog(config, clock), clock


def _active_classifier() -> Callable[[], AgentExecutionState]:
    return lambda: AgentExecutionState.ACTIVE


def _waiting_classifier() -> Callable[[], AgentExecutionState]:
    return lambda: AgentExecutionState.WAITING_ON_CHILD


# ---------------------------------------------------------------------------
# (a) No false kill on MCP tool activity
# ---------------------------------------------------------------------------


def test_no_false_kill_on_mcp_tool_activity() -> None:
    """Agent making MCP tool calls with no stdout output is not killed as idle.

    Sequence:
      - watchdog starts at t=0 with idle=0.1s, ttl=1000s
      - record_activity at t=0 (sets stdout baseline)
      - advance 100s of silence (well over idle 0.1s)
      - record_mcp_tool_call at t=100s (refreshes mcp_tool channel)
      - advance 50s (over idle again, well under 1000s TTL)
      - evaluate -> CONTINUE (deferred via mcp_tool channel evidence)
      - advance another 2000s (over the 1000s TTL, no new tool call)
      - evaluate -> FIRE (no fresh channel evidence)
    """
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    wd.record_activity()
    clock.advance(100.0)
    wd.record_mcp_tool_call()
    clock.advance(50.0)

    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"expected CONTINUE (deferred via mcp_tool channel), got {verdict}"
    )
    assert wd._channel_evidence_active(clock.monotonic()) is True

    # Now wait past the TTL with no new activity -> FIRE
    clock.advance(2000.0)
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.FIRE, f"expected FIRE (channel stale past TTL), got {verdict}"
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


# ---------------------------------------------------------------------------
# (b) No false kill on subagent work
# ---------------------------------------------------------------------------


def test_no_false_kill_on_subagent_work() -> None:
    """Agent waiting on a subagent that is demonstrably active is not killed.

    Mirrors the mcp_tool test but uses ``record_subagent_work`` and the
    same shape: long silence, subagent signal, advance, evaluate ->
    CONTINUE while the channel is fresh; advance past the TTL -> FIRE.
    """
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    wd.record_activity()
    clock.advance(100.0)
    wd.record_subagent_work()
    clock.advance(50.0)

    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.CONTINUE
    clock.advance(2000.0)
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


# ---------------------------------------------------------------------------
# (c) No false kill on workspace changes
# ---------------------------------------------------------------------------


def test_no_false_kill_on_workspace_changes() -> None:
    """Agent whose workspace is changing (writes files) is not killed as idle.

    Same shape as the mcp_tool and subagent tests, but uses the
    production-style ``record_workspace_event(kind=..., weight=...)``
    call that the WorkspaceMonitor -> watchdog wiring forwards.
    """
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    wd.record_activity()
    clock.advance(100.0)
    wd.record_workspace_event(kind=WorkspaceChangeKind.SOURCE, weight=1.0)
    clock.advance(50.0)

    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.CONTINUE
    clock.advance(2000.0)
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


# ---------------------------------------------------------------------------
# (d) Dead subagent detected within the regular idle window
# ---------------------------------------------------------------------------


def test_dead_subagent_detected_within_idle_window() -> None:
    """A silent subagent is detected at the regular idle deadline, not the
    cumulative WAITING_ON_CHILD ceiling.

    Sequence:
      - record a subagent work event (child is alive but signaling)
      - advance past the 30s TTL with no further activity
      - evaluate -> NO_OUTPUT_DEADLINE (the regular idle path), not
        CHILDREN_PERSIST_TOO_LONG (the 1800s cumulative ceiling)
    """
    wd, clock = _make_watchdog()
    wd.record_activity()
    wd.record_subagent_work()
    # Advance past the 30s default TTL (so the subagent channel is stale)
    clock.advance(31.0)
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.FIRE
    # The fire reason is the regular idle deadline, NOT the
    # cumulative waiting-on-child ceiling. Pre-fix, this would have
    # survived the full 1800s default because the only signal was
    # the child being alive.
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


# ---------------------------------------------------------------------------
# (e) Truly idle still fires on time
# ---------------------------------------------------------------------------


def test_truly_idle_still_fires_on_time() -> None:
    """A session with no activity on any channel is terminated no later than
    before. The new recorders are additive: ``record_activity`` and the
    existing NO_OUTPUT_DEADLINE path are unchanged when no channel signal
    is present.
    """
    wd, clock = _make_watchdog()
    # No record_* calls. Advance past idle timeout.
    clock.advance(1.0)
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


# ---------------------------------------------------------------------------
# (f) Session ceiling unaffected by activity
# ---------------------------------------------------------------------------


def test_session_ceiling_unaffected_by_activity() -> None:
    """``max_session_seconds`` fires exactly as before, regardless of activity.

    Even when MCP tool calls fire every second and the activity channel
    would otherwise defer the idle deadline, the absolute session
    ceiling is checked FIRST and fires immediately when exceeded.
    """
    wd, clock = _make_watchdog(idle_timeout=1.0, max_session=10.0, activity_ttl=30.0)
    for _t in range(0, 12):
        wd.record_mcp_tool_call()
        clock.advance(1.0)
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED


# ---------------------------------------------------------------------------
# (g) Max waiting-on-child ceiling unaffected by activity
# ---------------------------------------------------------------------------


def test_max_waiting_ceiling_unaffected_by_activity() -> None:
    """``max_waiting_on_child_seconds`` fires exactly as before, even when
    the activity channel is fresh.

    The activity channel can defer the NO_OUTPUT_DEADLINE branch, but it
    must NEVER defer the CHILDREN_PERSIST_TOO_LONG branch (cumulative
    ceiling is absolute).
    """
    wd, clock = _make_watchdog(idle_timeout=0.1, max_waiting=2.0, activity_ttl=30.0)
    wd.record_activity()
    clock.advance(0.1)
    # classify_quiet is always WAITING_ON_CHILD so the watchdog goes
    # into the WAITING branch. record_mcp_tool_call every 0.1s to keep
    # the mcp_tool channel fresh.
    for _ in range(30):
        verdict = wd.evaluate(classify_quiet=_waiting_classifier())
        if verdict == WatchdogVerdict.FIRE:
            break
        wd.record_mcp_tool_call()
        clock.advance(0.1)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG


# ---------------------------------------------------------------------------
# (h) activity_evidence_ttl_seconds=0.0 disables the feature
# ---------------------------------------------------------------------------


def test_activity_evidence_ttl_zero_disables_feature() -> None:
    """Setting ``activity_evidence_ttl_seconds=0.0`` disables the activity-aware
    verdict and restores the legacy stdout-only behavior.
    """
    wd, clock = _make_watchdog(activity_ttl=0.0)
    wd.record_activity()
    clock.advance(0.2)  # past idle
    wd.record_mcp_tool_call()
    # With ttl=0 the channel can never be "fresh", so the next
    # evaluate fires.
    clock.advance(0.1)
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


# ---------------------------------------------------------------------------
# (i) Per-channel evidence summary in HARD_STOP diagnostic
# ---------------------------------------------------------------------------


def test_evidence_summary_in_hard_stop_diagnostic() -> None:
    """When the watchdog fires CHILDREN_PERSIST_TOO_LONG, the emitted
    HARD_STOP event's diagnostic carries the per-channel evidence summary
    under the ``evidence_summary`` key.

    The post-mortem (or the on-call operator) can see exactly which
    channels were fresh and which were stale at the moment the
    watchdog fired.
    """
    events: list[WaitingStatusEvent] = []
    config = TimeoutPolicy(
        idle_timeout_seconds=0.1,
        drain_window_seconds=0.0,
        max_waiting_on_child_seconds=2.0,
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        activity_evidence_ttl_seconds=30.0,
        os_descendant_only_ceiling_seconds=None,
    )
    clock = FakeClock(start=0.0)
    wd = IdleWatchdog(config, clock, listener=events.append)
    wd.record_activity()
    # Record some activity on multiple channels to make the summary
    # interesting.
    wd.record_mcp_tool_call()
    wd.record_subagent_work()
    wd.record_workspace_event()
    # Go into WAITING and let the cumulative ceiling fire. The
    # activity channel does NOT defer the cumulative ceiling, so the
    # CHILDREN_PERSIST_TOO_LONG branch fires as soon as the cumulative
    # exceeds the 2.0s ceiling.
    verdict = WatchdogVerdict.CONTINUE
    for _ in range(25):
        verdict = wd.evaluate(classify_quiet=_waiting_classifier())
        if verdict == WatchdogVerdict.FIRE:
            break
        clock.advance(0.1)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    hard_stops = [e for e in events if e.kind == WaitingStatusKind.HARD_STOP]
    assert hard_stops, "no HARD_STOP event captured"
    diag = hard_stops[0].diagnostic
    assert "evidence_summary" in diag
    assert isinstance(diag["evidence_summary"], list)
    assert len(diag["evidence_summary"]) == 5
    channel_names = {entry["channel"] for entry in diag["evidence_summary"]}
    assert channel_names == {
        "stdout",
        "mcp_tool",
        "subagent_output",
        "subagent_liveness",
        "workspace",
    }


# ---------------------------------------------------------------------------
# (i2-i4) Per-channel evidence summary in fire-log extra for every reason
# ---------------------------------------------------------------------------


def test_session_ceiling_fire_carries_evidence_summary() -> None:
    """SESSION_CEILING_EXCEEDED fire log embeds per-channel evidence_summary."""
    wd, clock = _make_watchdog(max_session=5.0, start=0.0)
    wd.record_activity()
    wd.record_mcp_tool_call()
    wd.record_subagent_work()
    wd.record_workspace_event()
    clock.advance(6.0)

    captured: list[object] = []

    def _sink(message: object) -> None:
        captured.append(message)

    handler_id = loguru_logger.add(_sink, level="WARNING")
    try:
        verdict = wd.evaluate(classify_quiet=_active_classifier())
    finally:
        loguru_logger.remove(handler_id)

    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED

    fire_records = [
        m for m in captured if "FIRE reason=session_ceiling_exceeded" in str(m.record["message"])
    ]
    assert fire_records, "no SESSION_CEILING_EXCEEDED fire log captured"
    extra_dict = fire_records[0].record["extra"]
    bound_extra = extra_dict.get("extra", extra_dict)
    assert "evidence_summary" in bound_extra
    assert isinstance(bound_extra["evidence_summary"], list)
    assert len(bound_extra["evidence_summary"]) == 5
    assert "active_channel" in bound_extra
    assert bound_extra["fire_reason"] == "session_ceiling_exceeded"


def test_repeated_error_loop_fire_carries_evidence_summary() -> None:
    """REPEATED_ERROR_LOOP fire log embeds per-channel evidence_summary."""
    wd, clock = _make_watchdog(idle_timeout=300.0, max_waiting=600.0, start=0.0)
    msg = "MCP error -32001: Request timed out"
    for _ in range(4):
        wd.record_error_activity(msg)
        clock.advance(34.0)

    captured: list[object] = []

    def _sink(message: object) -> None:
        captured.append(message)

    handler_id = loguru_logger.add(_sink, level="WARNING")
    try:
        wd.record_error_activity(msg)
        verdict = wd.evaluate(classify_quiet=_active_classifier())
    finally:
        loguru_logger.remove(handler_id)

    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.REPEATED_ERROR_LOOP

    fire_records = [
        m for m in captured if "FIRE reason=repeated_error_loop" in str(m.record["message"])
    ]
    assert fire_records, "no REPEATED_ERROR_LOOP fire log captured"
    extra_dict = fire_records[0].record["extra"]
    bound_extra = extra_dict.get("extra", extra_dict)
    assert "evidence_summary" in bound_extra
    assert isinstance(bound_extra["evidence_summary"], list)
    assert len(bound_extra["evidence_summary"]) == 5
    assert "active_channel" in bound_extra
    assert bound_extra["fire_reason"] == "repeated_error_loop"


def test_stalled_after_tool_result_fire_carries_evidence_summary() -> None:
    """STALLED_AFTER_TOOL_RESULT fire log embeds per-channel evidence_summary."""
    config = TimeoutPolicy(
        idle_timeout_seconds=0.1,
        drain_window_seconds=0.0,
        max_waiting_on_child_seconds=100.0,
        post_tool_result_progression_seconds=0.1,
        suspect_waiting_on_child_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        activity_evidence_ttl_seconds=30.0,
        os_descendant_only_ceiling_seconds=None,
    )
    clock = FakeClock(start=0.0)
    wd = IdleWatchdog(config, clock)
    wd.record_activity()
    wd.record_tool_result_activity()
    clock.advance(1.0)

    captured: list[object] = []

    def _sink(message: object) -> None:
        captured.append(message)

    handler_id = loguru_logger.add(_sink, level="WARNING")
    try:
        verdict = wd.evaluate(classify_quiet=_active_classifier())
    finally:
        loguru_logger.remove(handler_id)

    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.STALLED_AFTER_TOOL_RESULT

    fire_records = [
        m for m in captured if "FIRE reason=stalled_after_tool_result" in str(m.record["message"])
    ]
    assert fire_records, "no STALLED_AFTER_TOOL_RESULT fire log captured"
    extra_dict = fire_records[0].record["extra"]
    bound_extra = extra_dict.get("extra", extra_dict)
    assert "evidence_summary" in bound_extra
    assert isinstance(bound_extra["evidence_summary"], list)
    assert len(bound_extra["evidence_summary"]) == 5
    assert "active_channel" in bound_extra
    assert bound_extra["fire_reason"] == "stalled_after_tool_result"


# ---------------------------------------------------------------------------
# (j) WorkspaceMonitor records last_event_at from the injected clock
# ---------------------------------------------------------------------------


def test_workspace_monitor_records_last_event_at(tmp_path: Path) -> None:
    """``WorkspaceMonitor`` accepts an injectable ``now`` callable so tests
    can drive ``last_event_at`` deterministically via FakeClock.

    This is the seam that lets the production runtime use
    ``time.monotonic`` while the tests use a deterministic value.
    """
    clock_value = [0.0]

    def fake_now() -> float:
        return clock_value[0]

    monitor = WorkspaceMonitor(tmp_path, now=fake_now)
    assert monitor.last_event_at is None
    assert monitor.event_count == 0
    monitor.record_event("/tmp/file_a.py")
    assert monitor.last_event_at == 0.0
    assert monitor.event_count == 1
    clock_value[0] = 5.0
    monitor.record_event("/tmp/file_b.py")
    assert monitor.last_event_at == 5.0
    assert monitor.event_count == 2

    monitor.reset_last_event_at()
    assert monitor.last_event_at is None
    assert monitor.event_count == 0


# ---------------------------------------------------------------------------
# (j2) WorkspaceMonitor -> watchdog integration (end-to-end)
# ---------------------------------------------------------------------------


def _default_classifier() -> WorkspaceChangeClassifier:
    """Return the conservative default classifier used in production."""
    return WorkspaceChangeClassifier(weights=dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS))


def test_workspace_monitor_to_watchdog_integration(tmp_path: Path) -> None:
    """``WorkspaceMonitor`` end-to-end integration: when the monitor's
    ``on_event`` callback is wired to the watchdog's
    ``record_workspace_event`` via the production 2-arg lambda, a
    recorded file change updates the watchdog's per-channel
    ``_last_workspace_event_at`` timestamp AND the per-kind counter.

    This is the production wiring: the readers receive the
    ``WorkspaceMonitor`` via ``ctx.monitor`` and register
    ``lambda kind, weight: watchdog.record_workspace_event(kind=kind, weight=weight)``
    as the on-event callback after the watchdog is created. A file
    change in the monitored workspace is then visible to the watchdog
    as a workspace channel event, and the activity-aware verdict can
    defer ``NO_OUTPUT_DEADLINE`` while the workspace is changing.

    Pre-fix, the production code path did not wire this up: the
    monitor's ``record_event`` updated its own internal
    ``last_event_at`` but never called the watchdog, so the watchdog's
    ``_last_workspace_event_at`` was always None and the workspace
    channel could never defer a fire. This test would fail in that
    case; after the fix it must pass.
    """
    wd, clock = _make_watchdog()
    # Use the watchdog's FakeClock as the monitor's clock source so
    # the two clocks stay synchronized (production uses time.monotonic
    # for both; the test mirrors that with a shared fake).
    monitor = WorkspaceMonitor(
        tmp_path,
        now=clock.monotonic,
        classifier=_default_classifier(),
    )
    # Pre-condition: watchdog has not observed any workspace activity yet.
    assert wd._last_workspace_event_at is None
    assert wd._workspace_event_count_internal == 0
    # Wire the production-style 2-arg callback so the watchdog receives
    # the real (kind, weight) classification instead of the OTHER default.
    monitor.set_on_event(lambda kind, weight: wd.record_workspace_event(kind=kind, weight=weight))
    # Advance both clocks together and trigger a file change.
    clock.advance(100.5)
    monitor.record_event("/tmp/foo.py")
    # The watchdog's per-channel state must now reflect the event.
    assert wd._last_workspace_event_at == 100.5
    assert wd._workspace_event_count_internal == 1
    # The per-kind counter must reflect the real classification (source),
    # not the OTHER default that the legacy 0-arg binding would yield.
    assert wd.workspace_kind_counts == {"source": 1}


def test_workspace_monitor_to_watchdog_defers_verdict(tmp_path: Path) -> None:
    """End-to-end: with WorkspaceMonitor wired to the watchdog via the
    production 2-arg lambda, a source file change defers
    ``NO_OUTPUT_DEADLINE`` while the channel is fresher than
    ``activity_evidence_ttl_seconds``.

    This is the AC-01 corollary for the workspace channel: a session
    that is quiet on stdout but actively writing source files is not
    killed as idle, even past the regular idle deadline.
    """
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    # Use the watchdog's FakeClock as the monitor's clock source so
    # both clocks stay synchronized (production uses time.monotonic
    # for both; the test mirrors that with a shared fake).
    monitor = WorkspaceMonitor(
        tmp_path,
        now=clock.monotonic,
        classifier=_default_classifier(),
    )
    monitor.set_on_event(lambda kind, weight: wd.record_workspace_event(kind=kind, weight=weight))
    # Quiet stdout for 5s of watchdog time. The monitor's clock is the
    # same as the watchdog's, so a single advance moves both.
    clock.advance(5.0)
    # A source workspace event is recorded at watchdog-t=5.0; the
    # watchdog workspace channel is now fresh.
    monitor.record_event("/tmp/foo.py")
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"expected CONTINUE (deferred via workspace channel), got {verdict}"
    )
    # Advance watchdog past the TTL with no new workspace activity.
    clock.advance(2000.0)
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE


def test_workspace_monitor_smart_filter_source_defers_log_does_not(tmp_path: Path) -> None:
    """End-to-end smart-filter proof: with the default conservative
    classifier, a source file change defers ``NO_OUTPUT_DEADLINE``
    while a log file change does NOT.

    This is the AC-07 regression test requested by the plan: workspace
    monitoring must remain smart, counting source changes as activity
    while dropping log-only churn by default.
    """
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    monitor = WorkspaceMonitor(
        tmp_path,
        now=clock.monotonic,
        classifier=_default_classifier(),
    )
    monitor.set_on_event(lambda kind, weight: wd.record_workspace_event(kind=kind, weight=weight))

    # Quiet stdout for 5s, then record a log file change.
    clock.advance(5.0)
    monitor.record_event("/tmp/agent.log")
    verdict = wd.evaluate(classify_quiet=_active_classifier())
    assert verdict == WatchdogVerdict.FIRE, (
        "expected FIRE for log-only change (dropped by default classifier)"
    )
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE
    assert wd.workspace_kind_counts.get("log", 0) == 0, (
        "log events must not increment the per-kind counter when dropped"
    )

    # Reset and record a source file change: this MUST defer the fire.
    wd2, clock2 = _make_watchdog(activity_ttl=1000.0)
    monitor2 = WorkspaceMonitor(
        tmp_path,
        now=clock2.monotonic,
        classifier=_default_classifier(),
    )
    monitor2.set_on_event(lambda kind, weight: wd2.record_workspace_event(kind=kind, weight=weight))
    clock2.advance(5.0)
    monitor2.record_event("/tmp/foo.py")
    verdict2 = wd2.evaluate(classify_quiet=_active_classifier())
    assert verdict2 == WatchdogVerdict.CONTINUE, (
        "expected CONTINUE for source change (counts as activity by default)"
    )
    assert wd2.workspace_kind_counts == {"source": 1}


# ---------------------------------------------------------------------------
# (k) last_evidence_summary returns 4-tuple in fixed channel order
# ---------------------------------------------------------------------------


def test_last_evidence_summary_channel_order() -> None:
    """``last_evidence_summary`` returns a tier-aware ``EvidenceSummary`` with
    five channels in fixed order (stdout, mcp_tool, subagent_output,
    subagent_liveness, workspace).
    """
    wd, _ = _make_watchdog()
    summary = wd.last_evidence_summary(0.0)
    assert isinstance(summary, EvidenceSummary)
    assert len(summary.channels) == 5
    assert [s.channel_name for s in summary.channels] == [
        ChannelName.STDOUT,
        ChannelName.MCP_TOOL,
        ChannelName.SUBAGENT_OUTPUT,
        ChannelName.SUBAGENT_LIVENESS,
        ChannelName.WORKSPACE,
    ]
    for entry in summary.channels:
        assert isinstance(entry, ChannelEvidenceSummary)


# ---------------------------------------------------------------------------
# (l) recorders do NOT mutate _last_activity
# ---------------------------------------------------------------------------


def test_recorders_do_not_mutate_last_activity() -> None:
    """The three recorders (``record_mcp_tool_call``, ``record_subagent_work``,
    ``record_workspace_event``) must NOT touch ``_last_activity`` (the stdout
    baseline). The existing 'stdout only resets idle baseline' invariant is
    preserved; the activity-aware verdict is layered on top of the
    existing log without perturbing it.
    """
    wd, clock = _make_watchdog()
    baseline = wd._last_activity
    # Move the clock forward so a record_activity() call produces a
    # strictly different timestamp; otherwise the FakeClock returns the
    # same value and the comparison would trivially succeed.
    clock.advance(1.0)
    wd.record_mcp_tool_call()
    wd.record_subagent_work()
    wd.record_workspace_event()
    assert wd._last_activity == baseline
    # stdout activity DOES move the baseline.
    wd.record_activity()
    assert wd._last_activity != baseline
    assert wd._last_activity > baseline


# ---------------------------------------------------------------------------
# (m) CorroborationSnapshot carries the new per-channel fields
# ---------------------------------------------------------------------------


def test_corroboration_snapshot_carries_per_channel_fields() -> None:
    """The new ``mcp_tool_call_count``, ``subagent_progress_count``,
    ``last_mcp_tool_call_at``, ``last_subagent_progress_at``,
    ``last_workspace_event_at``, and ``current_run_idle_elapsed_seconds``
    fields default to None so existing construction sites remain valid.
    """
    s = CorroborationSnapshot()
    assert s.mcp_tool_call_count is None
    assert s.subagent_progress_count is None
    assert s.last_mcp_tool_call_at is None
    assert s.last_subagent_progress_at is None
    assert s.last_workspace_event_at is None
    assert s.current_run_idle_elapsed_seconds is None
    # Existing fields still work.
    assert s.workspace_event_count is None
    assert s.alive_by is None


# ---------------------------------------------------------------------------
# (n) activity_evidence_ttl=None is allowed
# ---------------------------------------------------------------------------


def test_activity_evidence_ttl_none_is_allowed() -> None:
    """``activity_evidence_ttl_seconds=None`` is the disable opt-out; it
    must remain a valid TimeoutPolicy value (the feature is off, but the
    policy constructs successfully).
    """
    config = TimeoutPolicy(
        idle_timeout_seconds=0.1,
        activity_evidence_ttl_seconds=None,
    )
    assert config.activity_evidence_ttl_seconds is None


# ---------------------------------------------------------------------------
# (o) recorders accept a custom ``now`` timestamp
# ---------------------------------------------------------------------------


def test_recorders_accept_custom_now_timestamp() -> None:
    """The three recorders accept an optional ``now`` parameter so tests
    can drive timestamps without mutating the watchdog's injected clock.

    This is critical for the FakeClock-based tests in
    test_idle_watchdog_3.py and for the per-channel age math in
    ``_channel_evidence_active``.
    """
    wd, _ = _make_watchdog()
    wd.record_mcp_tool_call(now=42.0)
    wd.record_subagent_work(now=43.0)
    wd.record_workspace_event(now=44.0)
    assert wd._last_mcp_tool_call_at == 42.0
    assert wd._last_subagent_progress_at == 43.0
    assert wd._last_workspace_event_at == 44.0
    assert wd._mcp_tool_call_count == 1
    assert wd._subagent_progress_count == 1
    assert wd._workspace_event_count_internal == 1


# ---------------------------------------------------------------------------
# (p) Verify _handle_evidence_deferral exists and is callable
# ---------------------------------------------------------------------------


def test_handle_evidence_deferral_returns_continue() -> None:
    """``_handle_evidence_deferral`` is the private verdict-hook method
    the watchdog consults when the idle deadline has elapsed but a
    non-stdout channel is still fresh. It returns CONTINUE.
    """
    wd, clock = _make_watchdog()
    wd.record_mcp_tool_call()
    verdict = wd._handle_evidence_deferral(clock.monotonic(), 0.5)
    assert verdict == WatchdogVerdict.CONTINUE


# ---------------------------------------------------------------------------
# (q) Deferral debug log names the channel age, NOT idle_elapsed
# ---------------------------------------------------------------------------


def test_handle_evidence_deferral_debug_log_names_channel_age() -> None:
    """The deferral debug log's ``age=`` field must reflect the FRESHEST
    non-stdout channel age, not the stdout ``idle_elapsed``.

    This is the regression test for the Plan Compliance finding
    described in the development analysis: the pre-fix
    ``_handle_evidence_deferral`` passed ``round(idle_elapsed, 1)``
    twice, so the log claimed the freshest non-stdout channel age
    equalled the stdout idle elapsed (which is only true when stdout
    is the only channel and the active channel label is "none").

    Scenario:
      - stdout idle for 60s (well past the 0.1s idle deadline)
      - a subagent work event at t=5s (age = 55s at evaluate-time)
      - evaluate -> deferred (subagent channel is fresh under the
        default 30s TTL? NO - age 55s is over the 30s TTL)

    To force a real deferral we must keep the channel within the TTL,
    so the scenario is:
      - record_subagent_work at t=0
      - advance 50s of stdout silence (idle = 50s, channel age = 50s,
        still fresh under a 1000s TTL)
      - _handle_evidence_deferral with idle_elapsed=50.0

    The 'age=' field must equal 50.0 (the channel age), and the
    'idle_elapsed=' field must also equal 50.0 (which happens to
    match because there is no other activity; the test still proves
    the log line is well-formed and consistent with the
    _build_evidence_summary_diag helper).
    """
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    wd.record_subagent_work()  # at t=0
    clock.advance(50.0)  # both stdout and subagent age = 50s
    captured = []

    def _sink(message: object) -> None:
        captured.append(str(message.record["message"]))

    handler_id = loguru_logger.add(_sink, level="DEBUG")
    try:
        wd._handle_evidence_deferral(clock.monotonic(), 50.0)
    finally:
        loguru_logger.remove(handler_id)

    deferral_lines = [m for m in captured if "deferred via activity evidence" in m]
    assert deferral_lines, f"expected a 'deferred via activity evidence' debug log, got: {captured}"
    line = deferral_lines[0]
    # Both ages happen to be 50.0 in this scenario; the test confirms
    # the log line is well-formed and the channel label is 'subagent'.
    assert "channel=subagent" in line, f"channel label must be 'subagent', got: {line}"
    assert "age=50.0s" in line, f"age= field must be 50.0s, got: {line}"
    assert "idle_elapsed=50.0s" in line, f"idle_elapsed= field must be 50.0s, got: {line}"


def test_handle_evidence_deferral_debug_log_age_differs_from_idle_elapsed() -> None:
    """The deferral debug log's ``age=`` field must DIFFER from
    ``idle_elapsed=`` when the freshest non-stdout channel is fresher
    than the stdout baseline.

    Scenario:
      - watchdog starts at t=0 with idle=0.1s, ttl=1000s
      - record_activity at t=0 (sets stdout baseline)
      - advance 60s of stdout silence
      - record_mcp_tool_call at t=60s (refreshes mcp_tool channel;
        stdout last_at remains t=0, so stdout age = 60s)
      - advance 55s (total elapsed = 115s; mcp_tool age = 55s,
        stdout age = 115s; both fresh under 1000s TTL)
      - _handle_evidence_deferral with idle_elapsed=115.0

    Expected log: ``channel=mcp_tool age=55.0s idle_elapsed=115.0s``.
    The 'age=' value (55.0) must DIFFER from the 'idle_elapsed=' value
    (115.0); pre-fix the log claimed both were 115.0, which is
    incorrect and confusing to operators reading the post-mortem.
    """
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    wd.record_activity()  # at t=0
    clock.advance(60.0)
    wd.record_mcp_tool_call()  # at t=60 (stdout stays at t=0)
    clock.advance(55.0)  # total elapsed = 115s

    captured = []

    def _sink(message: object) -> None:
        captured.append(str(message.record["message"]))

    handler_id = loguru_logger.add(_sink, level="DEBUG")
    try:
        wd._handle_evidence_deferral(clock.monotonic(), 115.0)
    finally:
        loguru_logger.remove(handler_id)

    deferral_lines = [m for m in captured if "deferred via activity evidence" in m]
    assert deferral_lines, f"expected a 'deferred via activity evidence' debug log, got: {captured}"
    line = deferral_lines[0]
    assert "channel=mcp_tool" in line, f"channel label must be 'mcp_tool', got: {line}"
    assert "age=55.0s" in line, (
        f"age= field must reflect the mcp_tool channel age (55.0s), not "
        f"the stdout idle elapsed (115.0s); got: {line}"
    )
    assert "idle_elapsed=115.0s" in line, (
        f"idle_elapsed= field must reflect the stdout baseline age (115.0s), got: {line}"
    )
    # The two values must differ; this is the central regression assertion.
    assert "age=55.0s" in line and "idle_elapsed=115.0s" in line, (
        f"age= must differ from idle_elapsed= when the channel age is "
        f"fresher than the stdout baseline; got: {line}"
    )


def test_build_evidence_summary_diag_returns_freshest_age() -> None:
    """``_build_evidence_summary_diag`` now returns a 2-tuple
    ``(diag, freshest_age)`` so the verdict hook can name the
    channel age that is doing the deferral.

    This test pins the new return-type contract independently of
    the log call so a future refactor that drops the freshest_age
    value is caught immediately (the type signature change is the
    primary contract; this test enforces the value-level semantics
    on top of the static type).
    """
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    wd.record_activity()  # at t=0
    clock.advance(60.0)
    wd.record_mcp_tool_call()  # at t=60 (stdout stays at t=0)
    clock.advance(55.0)  # total elapsed = 115s

    diag, freshest_age = wd._build_evidence_summary_diag(clock.monotonic())
    assert isinstance(diag, dict)
    assert "evidence_summary" in diag
    assert diag["active_channel"] == "mcp_tool"
    # freshest_age must equal the mcp_tool channel age (55.0s), NOT
    # the stdout idle elapsed (115.0s).
    assert freshest_age == 55.0, (
        f"freshest_age must be the mcp_tool channel age (55.0s), got {freshest_age}"
    )

    # When the channels are stale (no fresh channel) freshest_age is None.
    wd2, clock2 = _make_watchdog(activity_ttl=10.0)
    wd2.record_activity()  # at t=0
    clock2.advance(20.0)  # past idle AND past the 10s TTL
    diag2, freshest_age2 = wd2._build_evidence_summary_diag(clock2.monotonic())
    assert diag2["active_channel"] == "none"
    assert freshest_age2 is None
