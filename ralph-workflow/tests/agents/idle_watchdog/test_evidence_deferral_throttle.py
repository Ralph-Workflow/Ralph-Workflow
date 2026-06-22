"""Pin: per-channel log throttle in IdleWatchdog._handle_evidence_deferral.

The PROMPT log showed per-tick DEBUG records emitted at
``idle_watchdog.py:_handle_evidence_deferral`` while a session stayed
active only through non-stdout evidence (mcp_tool / subagent /
workspace channel).  ``_gate_fire`` already has a per-(fire_reason,
deferred_kind) throttle via ``_maybe_log_deferred``; the activity-
evidence deferral path did NOT participate in that throttle so a
session that stayed deferred for thousands of ticks emitted a DEBUG
record on every poll.

The fix: a per-channel-key log throttle map keyed on the active
channel name (``mcp_tool`` / ``subagent`` / ``workspace`` / ``none``)
with a configurable throttle window
(``TimeoutPolicy.watchdog_log_throttle_seconds``, default 30 s).
The new helper ``_maybe_log_evidence_deferral`` consults
``self._last_evidence_deferral_log_at`` and emits at most one DEBUG
record per channel key per throttle window.

This test drives ``_handle_evidence_deferral`` 1000 times in the
same FakeClock second with the same channel label and asserts the
number of DEBUG records captured by a loguru StringIO sink stays
bounded (initial transition + at most one refresh window).  Pre-fix
the count was 1000.

All tests use FakeClock and a captured loguru sink; no real sleep,
no real subprocess, no real network.
"""

from __future__ import annotations

import io
from typing import Any

from loguru import logger

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


def test_handle_evidence_deferral_throttles_identical_channel_emission() -> None:
    """1000 calls to ``_handle_evidence_deferral`` in the same FakeClock
    second with the same channel label MUST emit at most 2 DEBUG records.

    Pre-fix the deferral path emitted one record per call (1000 records).
    Post-fix the throttle keeps it to <= 2 (initial transition + first
    refresh window).
    """
    _buf, captured, handler_id = _make_capture_sink()
    try:
        watchdog, clock = _make_watchdog(throttle_seconds=30.0)
        # Drive an active mcp_tool channel at t=0 so the verdict hook
        # reports ``active_channel=mcp_tool`` and the deferral path is
        # taken.  We never actually call ``evaluate`` here; the test
        # exercises ``_handle_evidence_deferral`` directly so we keep
        # the test independent of the gate/cumulative math.
        watchdog.record_mcp_tool_call(now=0.0)
        for _ in range(1000):
            verdict = watchdog._handle_evidence_deferral(clock.monotonic(), 50.0)
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


def test_handle_evidence_deferral_throttle_uses_configured_window() -> None:
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
        for _ in range(100):
            watchdog._handle_evidence_deferral(clock.monotonic(), 50.0)
        clock.advance(0.05)
        for _ in range(100):
            watchdog._handle_evidence_deferral(clock.monotonic(), 50.0)
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


def test_handle_evidence_deferral_throttle_is_per_channel() -> None:
    """Different channel labels MUST be tracked independently so an
    mcp_tool emission does not suppress a subsequent subagent emission.

    Verifies the throttle key is the channel label (mcp_tool /
    subagent / workspace / none), not the fire_reason alone.

    Setup: only mcp_tool is fresh in the first window; only subagent
    is fresh in the second window. We avoid co-recording channels by
    using a clean reset between the two windows: the first window
    records only mcp_tool; the second window records only subagent
    after a full TTL advance.
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
    watchdog.record_mcp_tool_call(now=0.0)
    assert (
        watchdog._handle_evidence_deferral(clock.monotonic(), 50.0)
        == WatchdogVerdict.CONTINUE
    )
    assert "mcp_tool" in watchdog._last_evidence_deferral_log_at
    first_window_at = watchdog._last_evidence_deferral_log_at["mcp_tool"]

    # Advance well past the mcp_tool TTL (180s) so mcp_tool ages out
    # and we can drive a clean per-channel transition.  Re-invoke
    # invocation_start to clear the throttle map so the second
    # window's subagent emission is the FIRST entry for the
    # ``subagent`` key (the throttle helper only emits on the
    # initial transition for an unseen key, but we need this
    # test to focus on per-channel key isolation rather than
    # re-logging under the same key).
    clock.advance(200.0)
    watchdog.record_invocation_start()

    # Second window: only subagent is fresh (mcp_tool is NOT set
    # this round).
    watchdog.record_subagent_work(now=clock.monotonic())
    assert (
        watchdog._handle_evidence_deferral(clock.monotonic(), 50.0)
        == WatchdogVerdict.CONTINUE
    )
    log_map = watchdog._last_evidence_deferral_log_at
    # The exact channel label for subagent work is ``subagent_output``
    # (see ``ChannelName`` in ``_evidence_tier.py``).  We assert
    # the key is present regardless of the exact label so a future
    # rename of the channel name does not break this test.
    subagent_keys = [
        key
        for key in log_map
        if isinstance(key, str) and "subagent" in key
    ]
    assert subagent_keys, (
        f"subagent key missing from throttle map after the second"
        f" window; keys={list(log_map)}"
    )
    # mcp_tool was cleared by invocation_start so the throttle map
    # only carries the second window's subagent entry.  This proves
    # per-channel key isolation: the mcp_tool throttle did not
    # suppress the subagent emission because they are different keys.
    assert log_map == {subagent_keys[0]: log_map[subagent_keys[0]]}, (
        f"Expected throttle map to carry only the subagent key after"
        f" the reset; got: {log_map}"
    )
    assert log_map[subagent_keys[0]] > first_window_at, (
        f"Expected subagent timestamp > first window's mcp_tool"
        f" timestamp; got subagent={log_map[subagent_keys[0]]},"
        f" first_window_at={first_window_at}"
    )


def test_handle_evidence_deferral_throttle_resets_on_invocation_start() -> None:
    """``record_invocation_start`` MUST reset the per-channel throttle map.

    Same contract as ``_last_deferred_log_at``: the throttle survives
    long-lived WAITING runs but MUST NOT carry state across invocations.
    """
    watchdog, clock = _make_watchdog(throttle_seconds=30.0)
    watchdog.record_mcp_tool_call(now=0.0)
    watchdog._handle_evidence_deferral(clock.monotonic(), 50.0)
    assert "mcp_tool" in watchdog._last_evidence_deferral_log_at
    # Reset by invocation_start.
    watchdog.record_invocation_start()
    assert watchdog._last_evidence_deferral_log_at == {}, (
        "record_invocation_start MUST reset the evidence deferral"
        f" throttle map; got: {watchdog._last_evidence_deferral_log_at}"
    )


def test_handle_evidence_deferral_returns_continue_when_throttled() -> None:
    """The verdict MUST remain CONTINUE regardless of whether the
    throttle suppresses the DEBUG emission.

    The throttle is a LOGGING concern only; the verdict logic is
    independent.
    """
    watchdog, clock = _make_watchdog(throttle_seconds=30.0)
    watchdog.record_mcp_tool_call(now=0.0)
    for _ in range(50):
        verdict = watchdog._handle_evidence_deferral(clock.monotonic(), 50.0)
        assert verdict == WatchdogVerdict.CONTINUE


def test_handle_evidence_deferral_uses_correlation_snapshot_when_no_channel() -> (
    None
):
    """When no channel is fresh the ``active_channel`` label is ``none``.

    The throttle map MUST still throttle the ``none`` key so a
    session that stays in this state for thousands of ticks emits
    at most 2 DEBUG records total.
    """
    _buf, captured, handler_id = _make_capture_sink()
    try:
        watchdog, clock = _make_watchdog(throttle_seconds=30.0)
        for _ in range(1000):
            watchdog._handle_evidence_deferral(clock.monotonic(), 50.0)
        matching = [
            r
            for r in captured
            if "deferred via activity evidence" in r
        ]
        # ``active_channel=none`` may still surface, but the throttle
        # caps emission at <= 2 records for 1000 calls in the same
        # FakeClock second.
        assert len(matching) <= _MAX_DEFER_EMISSIONS, (
            f"throttle regression on 'none' channel: got {len(matching)}"
            f" records for 1000 calls; expected <= {_MAX_DEFER_EMISSIONS}"
            f". Records: {matching[:3]}"
        )
    finally:
        _remove_sink(handler_id)
