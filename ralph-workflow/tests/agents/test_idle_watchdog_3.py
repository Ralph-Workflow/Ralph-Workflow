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

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    ChannelEvidenceSummary,
    CorroborationSnapshot,
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.invoke._workspace import WorkspaceMonitor
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

    Same shape as the mcp_tool and subagent tests, but uses
    ``record_workspace_event``.
    """
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    wd.record_activity()
    clock.advance(100.0)
    wd.record_workspace_event()
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
    """When the watchdog fires CHILDREN_PERSIST_TOO_LONG, the HARD_STOP
    diagnostic carries the per-channel evidence summary under the
    ``evidence_summary`` key.

    The post-mortem (or the on-call operator) can see exactly which
    channels were fresh and which were stale at the moment the
    watchdog fired.
    """
    wd, clock = _make_watchdog(idle_timeout=0.1, max_waiting=2.0, activity_ttl=30.0)
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
    for _ in range(25):
        verdict = wd.evaluate(classify_quiet=_waiting_classifier())
        if verdict == WatchdogVerdict.FIRE:
            break
        clock.advance(0.1)
    assert verdict == WatchdogVerdict.FIRE
    assert wd.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    summary = wd.last_evidence_summary(clock.monotonic())
    assert len(summary) == 4
    channel_names = {s.channel_name for s in summary}
    assert channel_names == {"stdout", "mcp_tool", "subagent", "workspace"}


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


def test_workspace_monitor_to_watchdog_integration(tmp_path: Path) -> None:
    """``WorkspaceMonitor`` end-to-end integration: when the monitor's
    ``on_event`` callback is wired to the watchdog's
    ``record_workspace_event``, a recorded file change updates the
    watchdog's per-channel ``_last_workspace_event_at`` timestamp.

    This is the production wiring: the readers receive the
    ``WorkspaceMonitor`` via ``ctx.monitor`` and register
    ``watchdog.record_workspace_event`` as the on-event callback after
    the watchdog is created. A file change in the monitored workspace
    is then visible to the watchdog as a workspace channel event,
    and the activity-aware verdict can defer ``NO_OUTPUT_DEADLINE``
    while the workspace is changing.

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
    monitor = WorkspaceMonitor(tmp_path, now=clock.monotonic)
    # Pre-condition: watchdog has not observed any workspace activity yet.
    assert wd._last_workspace_event_at is None
    assert wd._workspace_event_count_internal == 0
    # Wire the production-style callback: the monitor's on_event fires
    # the watchdog's recorder whenever a file change is observed.
    monitor.set_on_event(wd.record_workspace_event)
    # Advance both clocks together and trigger a file change.
    clock.advance(100.5)
    monitor.record_event("/tmp/foo.py")
    # The watchdog's per-channel state must now reflect the event.
    assert wd._last_workspace_event_at == 100.5
    assert wd._workspace_event_count_internal == 1


def test_workspace_monitor_to_watchdog_defers_verdict(tmp_path: Path) -> None:
    """End-to-end: with WorkspaceMonitor wired to the watchdog, an
    active workspace defers ``NO_OUTPUT_DEADLINE`` while the channel
    is fresher than ``activity_evidence_ttl_seconds``.

    This is the AC-01 corollary for the workspace channel: a session
    that is quiet on stdout but actively writing files is not killed
    as idle, even past the regular idle deadline.
    """
    wd, clock = _make_watchdog(activity_ttl=1000.0)
    # Use the watchdog's FakeClock as the monitor's clock source so
    # both clocks stay synchronized (production uses time.monotonic
    # for both; the test mirrors that with a shared fake).
    monitor = WorkspaceMonitor(tmp_path, now=clock.monotonic)
    monitor.set_on_event(wd.record_workspace_event)
    # Quiet stdout for 5s of watchdog time. The monitor's clock is the
    # same as the watchdog's, so a single advance moves both.
    clock.advance(5.0)
    # A workspace event is recorded at watchdog-t=5.0; the watchdog
    # workspace channel is now fresh.
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


# ---------------------------------------------------------------------------
# (k) last_evidence_summary returns 4-tuple in fixed channel order
# ---------------------------------------------------------------------------


def test_last_evidence_summary_channel_order() -> None:
    """``last_evidence_summary`` returns channels in fixed order
    (stdout, mcp_tool, subagent, workspace) so callers can index by
    position without lookup by name.
    """
    wd, _ = _make_watchdog()
    summary = wd.last_evidence_summary(0.0)
    assert isinstance(summary, tuple)
    assert len(summary) == 4
    assert [s.channel_name for s in summary] == [
        "stdout",
        "mcp_tool",
        "subagent",
        "workspace",
    ]
    for entry in summary:
        assert isinstance(entry, ChannelEvidenceSummary)
        assert entry.to_dict() == {
            "channel": entry.channel_name,
            "last_at": entry.last_at,
            "age_seconds": entry.age_seconds,
            "counter": entry.counter,
        }


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
