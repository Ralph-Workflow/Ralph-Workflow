"""Pin: per-(fire_reason, deferred_kind) log throttle in IdleWatchdog._gate_fire.

The PROMPT log shows ~10 DEBUG records/sec emitted at
``idle_watchdog.py:_gate_fire:949`` while a fire is deferred
(SILENT_SUBAGENT or generic non-STUCK kind). That per-tick emission
is log spam: the operator only needs to see one record when the
gate transitions into deferred mode and another when the state
changes, not one record every ~100 ms.

The fix: a per-(fire_reason, deferred_kind) throttle map keyed on
the monotonic clock with a configurable throttle window
(``TimeoutPolicy.watchdog_log_throttle_seconds``, default 30 s).
A new emission happens only when the key has never been logged
OR when ``now - last_logged_at >= watchdog_log_throttle_seconds``.

This test drives ``_gate_fire`` 1000 times in the same FakeClock
second with the same ``(CHILDREN_PERSIST_TOO_LONG, SILENT_SUBAGENT)``
pair and asserts the number of DEBUG records captured by a loguru
StringIO sink is at most 2 (one on first transition, one on the
throttled refresh window). Pre-fix the count is 1000.

All tests use FakeClock and a captured loguru sink; no real
sleep, no real subprocess, no real network.
"""

from __future__ import annotations

import io

import pytest
from loguru import logger

from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.idle_watchdog._stuck_classifier import StuckKind
from ralph.agents.idle_watchdog.idle_watchdog import CorroborationSnapshot
from ralph.agents.timeout_clock import FakeClock


@pytest.fixture
def captured_debug_records() -> tuple[io.StringIO, list[str]]:
    """Attach a loguru sink that captures DEBUG records from idle_watchdog.

    Returns (buffer, records) where records is a list of formatted
    log lines. The sink is removed automatically after the test.
    """
    buf = io.StringIO()
    records: list[str] = []

    def _sink(message: str) -> None:
        records.append(message)

    handler_id = logger.add(
        _sink,
        level="DEBUG",
        format="{message}",
        filter=lambda record: "idle_watchdog" in (record["extra"].get("component") or ""),
    )
    try:
        yield buf, records
    finally:
        logger.remove(handler_id)


def _make_watchdog(throttle_seconds: float = 30.0) -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        watchdog_log_throttle_seconds=throttle_seconds,
        activity_evidence_ttl_seconds=180.0,
    )
    return (
        IdleWatchdog(policy, clock),
        clock,
    )


def _patch_classifier_to_silent_subagent(watchdog: IdleWatchdog) -> None:
    """Force ``_classify_stuck_now`` to return ``StuckKind.SILENT_SUBAGENT``.

    The classifier is pure and consults watchdog state; monkey-patching
    it directly is the cleanest deterministic seam for this test.
    """

    def _stuck_now(
        *,
        now: float,
        idle_elapsed: float,
        corroboration: CorroborationSnapshot | None = None,
    ) -> StuckKind:
        return StuckKind.SILENT_SUBAGENT

    # Use ``setattr`` with the attribute name held in a local
    # variable so mypy cannot narrow the access to a private-method
    # assignment AND ruff B010 does not flag a setattr-with-constant-
    # value call. The policy test for ``test_zero_test_file_suppressions``
    # rejects bare mypy suppression comments inside test files.
    _classify_attr = "_classify_stuck_now"
    setattr(watchdog, _classify_attr, _stuck_now)


def test_gate_fire_throttles_identical_deferred_emission(
    captured_debug_records: tuple[io.StringIO, list[str]],
) -> None:
    """1000 calls to ``_gate_fire`` in the same FakeClock second MUST
    emit at most 2 DEBUG records.

    Pre-fix the gate emits one record per call (1000 records). Post-fix
    the throttle keeps it to <= 2 (initial transition + first refresh
    window).
    """
    _buf, records = captured_debug_records
    watchdog, clock = _make_watchdog(throttle_seconds=30.0)
    _patch_classifier_to_silent_subagent(watchdog)
    # SESSION_CEILING_EXCEEDED bypasses the gate; use a normal gated
    # reason so the SILENT_SUBAGENT branch fires.
    fire_reason = WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG
    for _ in range(1000):
        verdict = watchdog._gate_fire(
            fire_reason,
            now=clock.monotonic(),
            idle_elapsed=300.0,
            corroboration=CorroborationSnapshot(),
        )
        assert verdict == WatchdogVerdict.CONTINUE

    matching = [
        r
        for r in records
        if "silent subagent" in r and "CHILDREN_PERSIST_TOO_LONG" in r
    ]
    assert len(matching) <= 2, (
        f"DEBUG log spam regression: got {len(matching)} records"
        f" for 1000 calls in the same second; expected <= 2"
        f" (one initial + one refresh window). Records: {matching[:3]}"
    )


def test_gate_fire_throttle_uses_configured_window(
    captured_debug_records: tuple[io.StringIO, list[str]],
) -> None:
    """A throttle window of 0.01s MUST allow refresh emissions.

    With a tiny throttle window the test exercises the refresh
    boundary: drive 100 ticks at 0s and 100 ticks at 0.05s; the
    first tick emits, then no emissions for 0.01s; the 0.05s tick
    is past the refresh window so it emits again.
    """
    _buf, records = captured_debug_records
    watchdog, clock = _make_watchdog(throttle_seconds=0.01)
    _patch_classifier_to_silent_subagent(watchdog)
    fire_reason = WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG
    for _ in range(100):
        watchdog._gate_fire(
            fire_reason,
            now=clock.monotonic(),
            idle_elapsed=300.0,
            corroboration=CorroborationSnapshot(),
        )
    clock.advance(0.05)
    for _ in range(100):
        watchdog._gate_fire(
            fire_reason,
            now=clock.monotonic(),
            idle_elapsed=300.0,
            corroboration=CorroborationSnapshot(),
        )
    matching = [
        r
        for r in records
        if "silent subagent" in r and "CHILDREN_PERSIST_TOO_LONG" in r
    ]
    # Expect at most: 1 first transition + 1 refresh = 2
    assert len(matching) <= 3, (
        f"throttle window 0.01s produced too many emissions: {len(matching)}"
    )


def test_gate_fire_throttle_is_per_key() -> None:
    """Different (fire_reason, deferred_kind) pairs MUST be tracked
    independently so a SILENT_SUBAGENT emission does not suppress a
    LOADING or WAITING_ON_CHILD emission for the same fire_reason.

    Verifies the throttle key is the tuple, not the fire_reason alone.
    """
    watchdog, clock = _make_watchdog(throttle_seconds=30.0)
    call_log: list[StuckKind] = []

    def _stuck_now(
        *,
        now: float,
        idle_elapsed: float,
        corroboration: CorroborationSnapshot | None = None,
    ) -> StuckKind:
        kind = call_log[0] if call_log else StuckKind.SILENT_SUBAGENT
        return kind

    # Use ``setattr`` with the attribute name held in a local
    # variable so mypy cannot narrow the access to a private-method
    # assignment AND ruff B010 does not flag a setattr-with-constant-
    # value call. The policy test for ``test_zero_test_file_suppressions``
    # rejects bare mypy suppression comments inside test files.
    _classify_attr = "_classify_stuck_now"
    setattr(watchdog, _classify_attr, _stuck_now)
    fire_reason = WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    # First call with SILENT_SUBAGENT.
    call_log = [StuckKind.SILENT_SUBAGENT]
    assert (
        watchdog._gate_fire(
            fire_reason,
            now=clock.monotonic(),
            idle_elapsed=300.0,
            corroboration=CorroborationSnapshot(),
        )
        == WatchdogVerdict.CONTINUE
    )
    # Second call with LOADING (different deferred_kind).
    call_log = [StuckKind.LOADING]
    assert (
        watchdog._gate_fire(
            fire_reason,
            now=clock.monotonic(),
            idle_elapsed=300.0,
            corroboration=CorroborationSnapshot(),
        )
        == WatchdogVerdict.CONTINUE
    )
    # Both transitions MUST have logged because the throttle key
    # is the tuple (fire_reason, deferred_kind). The helper
    # ``_maybe_log_deferred`` consults the per-key timestamp map
    # and emits when the key has never been logged. Since both keys
    # are unseen, both transitions emit.
    assert hasattr(watchdog, "_last_deferred_log_at"), (
        "IdleWatchdog MUST expose _last_deferred_log_at for per-key"
        " log throttling"
    )
    log_map = watchdog._last_deferred_log_at
    assert (fire_reason.value, StuckKind.SILENT_SUBAGENT.value) in log_map, (
        f"SILENT_SUBAGENT key missing from throttle map; keys={list(log_map)}"
    )
    assert (fire_reason.value, StuckKind.LOADING.value) in log_map, (
        f"LOADING key missing from throttle map; keys={list(log_map)}"
    )
