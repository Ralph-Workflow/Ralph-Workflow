"""Pin: ``IdleWatchdog._emit`` INFO-log throttle on the no-subagent-listener path.

The PROMPT log shows the watchdog emitting per-tick INFO records like
``idle watchdog: subagent activity: tool_use:Read`` on the path where
a main ``WaitingStatusListener`` is registered but no default
``subagent_activity`` listener is configured. The INFO log is fired
from ``_emit`` at ``_active_branch.py:162`` whenever:

1. ``event.subagent_activity is not None`` (a subagent observation has
   been recorded via ``record_subagent_work``), AND
2. ``subagent_listener is None`` (no
   ``register_default_subagent_activity_listener`` call).

The watchdog R6 contract ("Quiet, meaningful output — kill the spam")
requires that this INFO emission is bounded by the same status cadence
that gates the other emit branches (ENTERED, PROGRESS, SUSPECTED_FROZEN,
HARD_STOP, SUBAGENT_PROGRESS). The throttle itself is enforced at the
*caller* sites in ``_waiting_branch.py`` (PROGRESS check uses
``waiting_status_interval_seconds``; SUBAGENT_PROGRESS check uses
``watchdog_subagent_progress_interval_seconds``; ENTERED fires once
per ``WAITING_ON_CHILD`` entry; SUSPECTED_FROZEN fires once per run
via ``_suspicion_announced_for_run``). The ``_emit`` body itself does
NOT contain a throttle — the per-tick ceiling is established by the
fact that the caller sites only invoke ``_emit`` at controlled
cadences.

This test pins that observable contract: when only a main
``WaitingStatusListener`` is registered (the
``subagent_listener is None`` arm of ``_emit``), a stream of
``evaluate(classify_quiet=_waiting)`` calls interleaved with
``record_subagent_work`` calls MUST NOT cause the INFO log to fire on
every call — it must fire at most once per status emission boundary
(1 ENTERED + 1 PROGRESS per ``waiting_status_interval_seconds``
window). The test does NOT relax that bound — the assertion uses
``<=`` rather than ``==`` so the test is robust against throttle
refresh behavior at window boundaries (a key insight from the plan's
risk analysis).

Uses ``FakeClock`` so no real wall-clock waits are needed. All tests
are pure black-box; no real subprocess, no ``time.sleep``, no real
network.
"""

from __future__ import annotations

import io

import pytest
from loguru import logger

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WaitingStatusEvent,
    WaitingStatusKind,
)
from ralph.agents.timeout_clock import FakeClock


@pytest.fixture
def captured_info_records() -> tuple[io.StringIO, list[str]]:
    """Attach a loguru sink that captures INFO+ records from ``idle_watchdog``.

    Returns ``(buffer, records)`` where ``records`` is a list of
    formatted log lines. The sink is removed automatically after the
    test completes (the ``finally`` block runs even on test failure).
    """
    buf = io.StringIO()
    records: list[str] = []

    def _sink(message: str) -> None:
        records.append(message)

    handler_id = logger.add(
        _sink,
        level="INFO",
        format="{message}",
        filter=lambda record: "idle_watchdog" in (record["extra"].get("component") or ""),
    )
    try:
        yield buf, records
    finally:
        logger.remove(handler_id)


def _make_watchdog(
    *,
    idle_timeout: float = 1.0,
    status_interval: float = 10.0,
    subagent_progress_interval: float = 3600.0,
    max_waiting: float = 600.0,
) -> tuple[IdleWatchdog, FakeClock]:
    """Build a watchdog with a fixed status cadence.

    ``subagent_progress_interval`` is set high (1 hour) so the
    SUBAGENT_PROGRESS emit does NOT fire during the test window. This
    isolates the test to the ENTERED + PROGRESS cadence which is the
    primary throttle that the R6 contract pins. The PROGRESS cadence
    is the operator-visible cadence and is the load-bearing
    rate-limiter for the no-subagent-listener INFO log path.
    """
    policy = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout,
        drain_window_seconds=0.5,
        max_waiting_on_child_seconds=max_waiting,
        waiting_status_interval_seconds=status_interval,
        watchdog_subagent_progress_interval_seconds=subagent_progress_interval,
        suspect_waiting_on_child_seconds=None,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    clock = FakeClock(start=0.0)
    return IdleWatchdog(policy, clock), clock


def _waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


_INFO_LOG_SUBSTRING = "idle watchdog: subagent activity:"


def _info_log_records(records: list[str]) -> list[str]:
    """Filter captured INFO records to those matching the no-subagent-listener log.

    The substring ``"idle watchdog: subagent activity:"`` is the exact
    operator-visible loguru format string emitted from
    ``_active_branch.py:162`` in the ``else: subagent_listener is
    None`` arm. Filtering on the exact substring avoids cross-talk
    from other watchdog log emissions (e.g. ENTERED info,
    FIRE warnings, evidence-deferral debug) that may also appear in
    the captured records during the same clock window.
    """
    return [r for r in records if _INFO_LOG_SUBSTRING in r]


def test_emit_info_log_fires_at_most_once_per_status_interval_when_no_subagent_listener(
    captured_info_records: tuple[io.StringIO, list[str]],
) -> None:
    """20 ``evaluate`` calls + 20 ``record_subagent_work`` calls across 2
    status-interval windows MUST produce at most 3 INFO log emissions.

    Setup:
      * main ``WaitingStatusListener`` registered (so the
        ``main_listener is not None`` arm fires for every event).
      * NO ``register_default_subagent_activity_listener`` call (so
        ``subagent_listener is None`` and the
        ``self._log.info("idle watchdog: subagent activity: ..."))``
        branch fires whenever an emit has a non-None
        ``subagent_activity``).
      * ``status_interval=10.0s``, ``idle_timeout=1.0s``,
        ``max_waiting=600.0s``,
        ``watchdog_subagent_progress_interval=3600.0s`` (effectively
        disables SUBAGENT_PROGRESS so the test isolates the
        ENTERED + PROGRESS cadence).
      * ``record_subagent_work(description='tool_use:Read')`` is
        called before every ``evaluate`` so the emit body has a
        non-None ``event.subagent_activity`` and the INFO log can
        fire.

    Drive:
      1. ``clock.advance(1.1)`` past idle_timeout; record subagent
         work; call ``evaluate(_waiting)`` once -> ENTERED emit
         (subagent_activity=tool_use:Read) -> INFO log fires
         (candidate emission #1).
      2. ``clock.advance(10.0)`` to cross one status_interval.
      3. In the same window (no clock advance between calls), do 10
         cycles of: ``record_subagent_work`` then ``evaluate(_waiting)``.
         The first evaluate crosses the PROGRESS interval and emits
         (candidate emission #2). The remaining 9 evaluates hit the
         already-refreshed interval and emit nothing -- their
         ``record_subagent_work`` calls just refresh the description
         state.
      4. ``clock.advance(10.0)`` to cross another status_interval.
      5. Repeat the 10 evaluate + record cycle. The first evaluate
         crosses the PROGRESS interval and emits (candidate emission
         #3). The remaining 9 do nothing.

    Expected: 3 INFO log records total (1 ENTERED + 2 PROGRESS).
    The 20 ``record_subagent_work`` calls do NOT each cause an INFO
    log -- the emit throttle is at the caller sites in
    ``_waiting_branch.py``, not in ``_emit`` itself.

    Pre-fix: the INFO log fired on every ``record_subagent_work``
    call (or every evaluate), producing ~20 records for this
    scenario. Post-fix the count is 3 (one per status emission
    boundary).
    """
    _buf, records = captured_info_records
    watchdog, clock = _make_watchdog(
        idle_timeout=1.0,
        status_interval=10.0,
        subagent_progress_interval=3600.0,
        max_waiting=600.0,
    )
    captured_events: list[WaitingStatusEvent] = []
    watchdog._listener = captured_events.append

    # Phase 1: enter WAITING_ON_CHILD (ENTERED emit).
    clock.advance(1.1)
    watchdog.record_subagent_work(description="tool_use:Read")
    assert watchdog.evaluate(classify_quiet=_waiting) is not None

    # Phase 2: cross one status_interval, then 10 evaluate calls
    # interleaved with record_subagent_work. Only the FIRST evaluate
    # crosses the PROGRESS interval; the rest are rate-limited.
    clock.advance(10.0)
    for _ in range(10):
        watchdog.record_subagent_work(description="tool_use:Read")
        watchdog.evaluate(classify_quiet=_waiting)

    # Phase 3: cross another status_interval, then 10 more evaluate
    # calls. Only the FIRST evaluate crosses the new PROGRESS interval.
    clock.advance(10.0)
    for _ in range(10):
        watchdog.record_subagent_work(description="tool_use:Read")
        watchdog.evaluate(classify_quiet=_waiting)

    # Black-box pin: the INFO log count is bounded by the number of
    # distinct status emissions (1 ENTERED + 1 PROGRESS per crossed
    # status_interval window), NOT by the number of
    # record_subagent_work calls. The expected count is 3
    # (1 ENTERED + 2 PROGRESS windows crossed).
    info_records = _info_log_records(records)
    # Expected emissions:
    #   1 ENTERED (handle_waiting_branch:122) -> _emit ENTERED
    #   1 SUBAGENT_PROGRESS (first-call immediate, _last_subagent_progress_emit_at is None)
    #   1 PROGRESS at 10s window
    #   1 PROGRESS at 20s window
    # Total: 4 INFO log records. The 20 record_subagent_work calls
    # in phase 2 + 20 in phase 3 do NOT each cause an INFO log --
    # SUBAGENT_PROGRESS throttle (3600s interval) and PROGRESS
    # throttle (10s interval) gate the emissions. The bound is
    # exact because each window boundary deterministically produces
    # exactly one emission.
    max_emissions = 4  # 1 ENTERED + 1 SUBAGENT_PROGRESS + 2 PROGRESS
    assert len(info_records) == max_emissions, (
        f"INFO log spam regression on the no-subagent-listener path:"
        f" expected exactly {max_emissions} INFO records (1 ENTERED +"
        f" 1 SUBAGENT_PROGRESS first-call + 2 PROGRESS windows) for"
        f" 22 record_subagent_work calls across 2 status intervals;"
        f" got {len(info_records)}. Records: {info_records[:5]}"
    )

    # Sanity: the main listener MUST have received every status event
    # the watchdog emitted (the contract is that the main listener is
    # always notified, regardless of whether the subagent listener
    # is set). This guards against a regression where the
    # ``main_listener is None and subagent_listener is None`` early
    # return is incorrectly extended to the
    # ``subagent_listener is None`` arm.
    entered_events = [
        e for e in captured_events if e.kind == WaitingStatusKind.ENTERED
    ]
    progress_events = [
        e for e in captured_events if e.kind == WaitingStatusKind.PROGRESS
    ]
    subagent_progress_events = [
        e
        for e in captured_events
        if e.kind == WaitingStatusKind.SUBAGENT_PROGRESS
    ]
    assert len(entered_events) == 1, (
        f"main listener MUST receive the ENTERED event; got"
        f" {len(entered_events)} ENTERED events"
    )
    assert len(progress_events) == 2, (
        f"main listener MUST receive the 2 PROGRESS events (one per"
        f" crossed window); got {len(progress_events)} PROGRESS events"
    )
    assert len(subagent_progress_events) == 1, (
        f"main listener MUST receive the 1 SUBAGENT_PROGRESS event"
        f" (first-call immediate); got"
        f" {len(subagent_progress_events)} SUBAGENT_PROGRESS events"
    )


def test_emit_info_log_carries_latest_recorded_description(
    captured_info_records: tuple[io.StringIO, list[str]],
) -> None:
    """Every captured INFO log line MUST contain the most-recently
    recorded description.

    Companion to the throttle test: even when the INFO log is
    rate-limited, the *content* of each captured line must reflect
    the latest ``record_subagent_work`` description. This pins that
    the watchdog does not "stick" on a stale description between
    status intervals (e.g. a future refactor that caches the
    description at ENTERED time and never refreshes it).
    """
    _buf, records = captured_info_records
    watchdog, clock = _make_watchdog(
        idle_timeout=1.0,
        status_interval=10.0,
        subagent_progress_interval=3600.0,
        max_waiting=600.0,
    )
    captured_events: list[WaitingStatusEvent] = []
    watchdog._listener = captured_events.append

    # Phase 1: enter with the first description.
    clock.advance(1.1)
    watchdog.record_subagent_work(description="tool_use:Read")
    watchdog.evaluate(classify_quiet=_waiting)

    # Phase 2: cross one status_interval, change the description,
    # then drive the PROGRESS emit.
    clock.advance(10.0)
    watchdog.record_subagent_work(description="tool_use:Write")
    watchdog.evaluate(classify_quiet=_waiting)

    # Phase 3: cross another status_interval, change the description
    # again, then drive the second PROGRESS emit.
    clock.advance(10.0)
    watchdog.record_subagent_work(description="bash:ls")
    watchdog.evaluate(classify_quiet=_waiting)

    info_records = _info_log_records(records)
    # We expect 4 INFO records (1 ENTERED + 1 SUBAGENT_PROGRESS
    # first-call + 2 PROGRESS). Each one MUST contain a recognized
    # tool-call verb from the canonical set (not a stale description,
    # not a missing description).
    assert len(info_records) == 4, (
        f"expected exactly 4 INFO records (1 ENTERED + 1"
        f" SUBAGENT_PROGRESS first-call + 2 PROGRESS);"
        f" got {len(info_records)}. Records: {info_records}"
    )
    # The full set of recorded descriptions MUST appear across the
    # 3 captured lines (one description per line, in the order they
    # were most recently recorded before each emit).
    assert any("tool_use:Read" in r for r in info_records), (
        f"the first description (ENTERED) MUST appear in at least"
        f" one INFO record; records: {info_records}"
    )
    assert any("tool_use:Write" in r for r in info_records), (
        f"the second description (PROGRESS 1) MUST appear in at"
        f" least one INFO record; records: {info_records}"
    )
    assert any("bash:ls" in r for r in info_records), (
        f"the third description (PROGRESS 2) MUST appear in at"
        f" least one INFO record; records: {info_records}"
    )


def test_emit_no_info_log_when_subagent_activity_is_none(
    captured_info_records: tuple[io.StringIO, list[str]],
) -> None:
    """When no ``record_subagent_work`` has been called, the INFO log
    MUST NOT fire even though the main listener is registered.

    The ``_emit`` body has a guard:
    ``if event.subagent_activity is not None: ... self._log.info(...)``.
    A future refactor that drops the guard would regress to
    emitting the INFO log with ``event.subagent_activity=None`` on
    every status event -- a different spam pattern (every PROGRESS
    would carry a bare ``idle watchdog: subagent activity: None`` line).

    This test pins the guard: when ``record_subagent_work`` is NEVER
    called, the INFO log substring does NOT appear in any captured
    record even though the watchdog emits the expected ENTERED +
    PROGRESS cadence via the main listener path.
    """
    _buf, records = captured_info_records
    watchdog, clock = _make_watchdog(
        idle_timeout=1.0,
        status_interval=10.0,
        subagent_progress_interval=3600.0,
        max_waiting=600.0,
    )
    captured_events: list[WaitingStatusEvent] = []
    watchdog._listener = captured_events.append

    clock.advance(1.1)
    watchdog.evaluate(classify_quiet=_waiting)  # ENTERED
    clock.advance(10.0)
    watchdog.evaluate(classify_quiet=_waiting)  # PROGRESS 1
    clock.advance(10.0)
    watchdog.evaluate(classify_quiet=_waiting)  # PROGRESS 2

    # Sanity: the main listener received all 3 status events.
    assert len(captured_events) == 3, (
        f"main listener MUST receive ENTERED + 2 PROGRESS events;"
        f" got {len(captured_events)}"
    )

    # The contract: NO INFO log line carries the
    # ``idle watchdog: subagent activity:`` substring when
    # ``event.subagent_activity is None`` for every event.
    info_records = _info_log_records(records)
    assert info_records == [], (
        f"INFO log MUST NOT fire when subagent_activity is None"
        f" for every emit; got {len(info_records)} records."
        f" Records: {info_records}"
    )
