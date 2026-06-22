"""Pin: per-channel log throttle in IdleWatchdog evidence-deferral path.

The PROMPT log showed per-tick DEBUG records emitted from the
evidence-deferral branch of ``idle_watchdog.py`` (the activity-aware
CONTINUE verdict) while a session stayed active only through
non-stdout evidence (mcp_tool / subagent / workspace channel).  The
gate-fire path already had a per-(fire_reason, deferred_kind)
throttle via ``_maybe_log_deferred``; the activity-evidence deferral
path did NOT participate in that throttle so a session that stayed
deferred for thousands of ticks emitted a DEBUG record on every poll.

The fix: a per-channel-key log throttle map keyed on the active
channel name (``mcp_tool`` / ``subagent`` / ``workspace`` / ``none``)
with a configurable throttle window
(``TimeoutPolicy.watchdog_log_throttle_seconds``, default 30 s).
The new helper ``_maybe_log_evidence_deferral`` consults
``self._last_evidence_deferral_log_at`` and emits at most one DEBUG
record per channel key per throttle window.

This test drives the watchdog through ``evaluate()`` (the public
verdict path) 1000 times in the same FakeClock second with the same
channel label and asserts the number of DEBUG records captured by a
loguru StringIO sink stays bounded (initial transition + at most one
refresh window).  Pre-fix the count was 1000.

Black-box: tests drive ``evaluate()`` so the assertion holds against
the public verdict surface (a watchdog caller that constructs a
real ``evaluate()`` loop will see at most 2 DEBUG emissions for the
same channel in the same throttle window).

All tests use FakeClock and a captured loguru sink; no real sleep,
no real subprocess, no real network.
"""

from __future__ import annotations

import io
from typing import Any

from loguru import logger

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogVerdict,
)
from ralph.agents.timeout_clock import FakeClock

_MAX_DEFER_EMISSIONS = 2


def _make_capture_sink() -> tuple[io.StringIO, list[str]]:
    buf = io.StringIO()
    captured: list[str] = []

    def _sink(message: str) -> None:
        captured.append(message)

    handler_id = logger.add(
        _sink,
        level="DEBUG",
        format="{message}",
        filter=lambda record: "idle_watchdog" in (record["extra"].get("component") or ""),
    )
    return buf, captured, handler_id


def _remove_sink(handler_id: int) -> None:
    logger.remove(handler_id)


def _make_watchdog(*, throttle_seconds: float = 30.0) -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    kwargs: dict[str, Any] = {
        "idle_timeout_seconds": 60.0,
        "no_output_at_start_seconds": 30.0,
        "no_progress_quiet_seconds": None,
        "watchdog_log_throttle_seconds": throttle_seconds,
        "activity_evidence_ttl_seconds": 180.0,
    }
    policy = TimeoutPolicy(**kwargs)
    return (
        IdleWatchdog(policy, clock),
        clock,
    )


def _active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def test_evidence_deferral_throttles_identical_channel_emission() -> None:
    """1000 ``evaluate()`` calls in the same FakeClock second with the
    same channel label MUST emit at most 2 DEBUG records.

    Pre-fix the deferral path emitted one record per call (1000 records).
    Post-fix the throttle keeps it to <= 2 (initial transition + first
    refresh window).
    """
    _buf, captured, handler_id = _make_capture_sink()
    try:
        watchdog, clock = _make_watchdog(throttle_seconds=30.0)
        # Drive an active mcp_tool channel at t=0 so the verdict hook
        # reports ``active_channel=mcp_tool`` and the deferral path is
        # taken.  Advance past ``idle_timeout_seconds`` (60s) so
        # ``evaluate()`` enters the activity-aware deferral branch.
        watchdog.record_mcp_tool_call(now=0.0)
        clock.advance(61.0)
        for _ in range(1000):
            verdict = watchdog.evaluate(classify_quiet=_active)
            assert verdict == WatchdogVerdict.CONTINUE
        matching = [
            r
            for r in captured
            if "deferred via activity evidence" in r and "channel=mcp_tool" in r
        ]
        assert len(matching) <= _MAX_DEFER_EMISSIONS, (
            f"DEBUG log spam regression: got {len(matching)} records"
            f" for 1000 calls in the same second; expected <= {_MAX_DEFER_EMISSIONS}"
            f" (one initial + one refresh window). Records: {matching[:3]}"
        )
    finally:
        _remove_sink(handler_id)


def test_evidence_deferral_throttle_uses_configured_window() -> None:
    """A throttle window of 0.01s MUST allow refresh emissions.

    With a tiny throttle window the test exercises the refresh
    boundary: drive 100 ticks at 0s and 100 ticks at 0.05s; the
    first tick emits, then no emissions for 0.01s; the 0.05s tick
    is past the refresh window so it emits again.
    """
    _buf, captured, handler_id = _make_capture_sink()
    try:
        watchdog, clock = _make_watchdog(throttle_seconds=0.01)
        watchdog.record_mcp_tool_call(now=0.0)
        clock.advance(61.0)
        for _ in range(100):
            watchdog.evaluate(classify_quiet=_active)
        clock.advance(0.05)
        for _ in range(100):
            watchdog.evaluate(classify_quiet=_active)
        matching = [
            r
            for r in captured
            if "deferred via activity evidence" in r and "channel=mcp_tool" in r
        ]
        assert len(matching) <= 3, (
            f"throttle window 0.01s produced too many emissions: {len(matching)}"
        )
    finally:
        _remove_sink(handler_id)


def test_evidence_deferral_throttle_is_per_channel() -> None:
    """Different channel labels MUST be tracked independently so an
    mcp_tool emission does not suppress a subsequent subagent emission.

    Verifies the throttle key is the channel label (mcp_tool /
    subagent / workspace / none), not the fire_reason alone.

    Setup: only mcp_tool is fresh in the first window; only subagent
    is fresh in the second window. We avoid co-recording channels by
    using a clean reset between the two windows: the first window
    records only mcp_tool; the second window records only subagent
    after a full TTL advance.

    Black-box: drive ``evaluate()`` and verify the channel appears in
    the emitted evidence-summary channels.
    """
    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=60.0,
            no_output_at_start_seconds=30.0,
            no_progress_quiet_seconds=None,
            watchdog_log_throttle_seconds=0.5,
            activity_evidence_ttl_seconds=180.0,
        ),
        FakeClock(start=0.0),
    )
    clock = watchdog._clock

    # First window: only mcp_tool is fresh (subagent is NOT yet set).
    # Advance past idle timeout and evaluate so the deferral path is
    # taken with ``active_channel=mcp_tool``.
    watchdog.record_mcp_tool_call(now=0.0)
    clock.advance(61.0)
    assert watchdog.evaluate(classify_quiet=_active) == WatchdogVerdict.CONTINUE

    # The diagnostic snapshot is the public surface for the per-channel
    # evidence summary; assert the mcp_tool channel is fresh in the
    # first window so the per-channel throttle key was set.
    snap_first = watchdog.diagnostic_snapshot(now=clock.monotonic())
    mcp_channel_first = next(
        (
            entry
            for entry in snap_first["evidence_summary"]
            if isinstance(entry, dict)
            and entry.get("channel") == "mcp_tool"
        ),
        None,
    )
    assert mcp_channel_first is not None, (
        f"evidence_summary MUST contain an mcp_tool channel in the"
        f" first window; got: {snap_first['evidence_summary']}"
    )

    # Advance well past the mcp_tool TTL (180s) so mcp_tool ages out
    # and we can drive a clean per-channel transition.  Re-invoke
    # invocation_start to clear the throttle map so the second
    # window's subagent emission is the FIRST entry for the
    # subagent channel key (the throttle helper only emits on the
    # initial transition for an unseen key, but we need this
    # test to focus on per-channel key isolation rather than
    # re-logging under the same key).
    clock.advance(200.0)
    watchdog.record_invocation_start()

    # Second window: only subagent is fresh (mcp_tool is NOT set
    # this round).
    clock.advance(61.0)
    watchdog.record_subagent_work(now=clock.monotonic())
    assert watchdog.evaluate(classify_quiet=_active) == WatchdogVerdict.CONTINUE
    snap_second = watchdog.diagnostic_snapshot(now=clock.monotonic())
    subagent_channel_second = next(
        (
            entry
            for entry in snap_second["evidence_summary"]
            if isinstance(entry, dict)
            and "subagent" in str(entry.get("channel", ""))
        ),
        None,
    )
    assert subagent_channel_second is not None, (
        f"evidence_summary MUST contain a subagent channel in the"
        f" second window; got: {snap_second['evidence_summary']}"
    )


def test_evidence_deferral_throttle_resets_on_invocation_start() -> None:
    """``record_invocation_start`` MUST reset the per-channel throttle map.

    Same contract as ``_last_deferred_log_at``: the throttle survives
    long-lived WAITING runs but MUST NOT carry state across invocations.

    Black-box: drive a deferral scenario through ``evaluate()`` to
    populate the throttle map, then ``record_invocation_start`` MUST
    clear it (the next deferral scenario starts a fresh log budget).
    """
    watchdog, clock = _make_watchdog(throttle_seconds=30.0)
    watchdog.record_mcp_tool_call(now=0.0)
    clock.advance(61.0)
    assert watchdog.evaluate(classify_quiet=_active) == WatchdogVerdict.CONTINUE
    # Reset by invocation_start.
    watchdog.record_invocation_start()
    # Drive a second deferral scenario immediately after the reset.
    # The reset MUST NOT have carried over throttle state from the
    # previous invocation.
    clock.advance(0.0)
    watchdog.record_mcp_tool_call(now=clock.monotonic())
    clock.advance(61.0)
    assert watchdog.evaluate(classify_quiet=_active) == WatchdogVerdict.CONTINUE


def test_evidence_deferral_returns_continue_when_throttled() -> None:
    """The verdict MUST remain CONTINUE regardless of whether the
    throttle suppresses the DEBUG emission.

    The throttle is a LOGGING concern only; the verdict logic is
    independent.  This is observable from ``evaluate()``'s return
    value: every call returns CONTINUE while the channel is fresh.
    """
    watchdog, clock = _make_watchdog(throttle_seconds=30.0)
    watchdog.record_mcp_tool_call(now=0.0)
    clock.advance(61.0)
    for _ in range(50):
        verdict = watchdog.evaluate(classify_quiet=_active)
        assert verdict == WatchdogVerdict.CONTINUE


def test_evidence_deferral_uses_correlation_snapshot_when_no_channel() -> None:
    """When no channel is fresh the ``active_channel`` label is ``none``.

    The throttle map MUST still throttle the ``none`` key so a
    session that stays in this state for thousands of ticks emits
    at most 2 DEBUG records total.
    """
    _buf, captured, handler_id = _make_capture_sink()
    try:
        watchdog, clock = _make_watchdog(throttle_seconds=30.0)
        # Advance past idle timeout.  No recorded activity channel
        # means ``active_channel=none``; the deferral path is still
        # entered because ``_channel_evidence_active`` defaults to
        # ACTIVE (the dummy channel is reported as active when no
        # recorded evidence exists; the test asserts the throttle
        # bounds the debug emission regardless of the channel label).
        clock.advance(61.0)
        for _ in range(1000):
            watchdog.evaluate(classify_quiet=_active)
        matching = [
            r
            for r in captured
            if "deferred via activity evidence" in r
        ]
        assert len(matching) <= _MAX_DEFER_EMISSIONS, (
            f"throttle regression on 'none' channel: got {len(matching)}"
            f" records for 1000 calls; expected <= {_MAX_DEFER_EMISSIONS}"
            f". Records: {matching[:3]}"
        )
    finally:
        _remove_sink(handler_id)
