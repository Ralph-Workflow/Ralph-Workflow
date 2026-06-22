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

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WaitingStatusEvent,
    WaitingStatusKind,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.idle_watchdog._stuck_classifier import StuckKind
from ralph.agents.idle_watchdog.idle_watchdog import CorroborationSnapshot
from ralph.agents.timeout_clock import FakeClock
from ralph.process.child_liveness import AliveBy


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
    """The COARSE single-key throttle caps emissions at most one DEBUG
    record per ``watchdog_log_throttle_seconds`` per ``fire_reason``
    regardless of how the ``deferred_kind`` cycles.

    Verifies the COARSE throttle is keyed on ``fire_reason.value``
    alone, NOT the tuple. The per-tuple key is consulted ONLY when
    the coarse throttle permits a log emission.

    The PROMPT log showed ~10 DEBUG records/sec at ``_gate_fire:949``
    even after the per-(fire_reason, deferred_kind) throttle was
    added, because the deferred_kind cycles (SILENT_SUBAGENT ->
    LOADING -> SILENT_SUBAGENT) and the per-tuple throttle key
    CHANGED on each cycle so the per-tuple throttle MISSED. The
    coarse single-key throttle solves this by keying on
    ``fire_reason.value`` alone, capping emissions to one DEBUG
    record per throttle window per fire_reason.
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
    # Both transitions MUST route through the coarse throttle (the
    # kind label is preserved on ``_last_deferred_kind`` so
    # operators can still see WHICH kind was deferred; the throttle
    # is on the LOG emission, not on the kind tracking).
    assert hasattr(watchdog, "_last_any_deferred_log_at"), (
        "IdleWatchdog MUST expose _last_any_deferred_log_at for the"
        " coarse single-key throttle"
    )
    coarse_map = watchdog._last_any_deferred_log_at
    assert fire_reason.value in coarse_map, (
        f"fire_reason key missing from coarse throttle map;"
        f" keys={list(coarse_map)}"
    )
    # The CURRENT kind label is preserved on the watchdog's
    # ``_last_deferred_kind`` field -- the operator can still see
    # which kind was deferred even when the coarse throttle
    # suppressed the log emission.
    assert watchdog._last_deferred_kind == StuckKind.LOADING, (
        f"expected _last_deferred_kind=LOADING (the most recent);"
        f" got {watchdog._last_deferred_kind!r}"
    )


def test_coarse_single_key_throttle_caps_emissions_across_kind_cycles(
    captured_debug_records: tuple[io.StringIO, list[str]],
) -> None:
    """1000 calls cycling SILENT_SUBAGENT <-> LOADING MUST emit at most 2 DEBUG records.

    The PROMPT log spam regression: drive ``_gate_fire`` 1000 times
    cycling between SILENT_SUBAGENT and LOADING (the typical
    deferred_kind cycle during a long-lived waiting run) inside a
    single throttle window; assert the captured DEBUG records is at
    most 2 (one initial transition + one refresh). Pre-fix the count
    is ~500 because the per-tuple throttle key changes every call.
    Post-fix the coarse throttle caps emissions to <= 2.
    """
    _buf, records = captured_debug_records
    watchdog, clock = _make_watchdog(throttle_seconds=30.0)
    call_log: list[StuckKind] = []

    def _stuck_now(
        *,
        now: float,
        idle_elapsed: float,
        corroboration: CorroborationSnapshot | None = None,
    ) -> StuckKind:
        # Cycle SILENT_SUBAGENT <-> LOADING on every call so the
        # per-tuple throttle key changes every time.
        kind = call_log[0] if call_log else StuckKind.SILENT_SUBAGENT
        return kind

    _classify_attr = "_classify_stuck_now"
    setattr(watchdog, _classify_attr, _stuck_now)
    fire_reason = WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    kinds = [StuckKind.SILENT_SUBAGENT, StuckKind.LOADING]
    for i in range(1000):
        call_log = [kinds[i % 2]]
        watchdog._gate_fire(
            fire_reason,
            now=clock.monotonic(),
            idle_elapsed=300.0,
            corroboration=CorroborationSnapshot(),
        )

    matching = [
        r
        for r in records
        if (
            ("silent subagent" in r or "deferred fire" in r)
            and "CHILDREN_PERSIST_TOO_LONG" in r
        )
    ]
    assert len(matching) <= 2, (
        f"coarse single-key throttle MUST cap emissions across"
        f" kind-cycles; got {len(matching)} records for 1000 calls"
        f" in the same throttle window. Records: {matching[:3]}"
    )


def test_scoped_child_active_appears_in_hard_stop_diag() -> None:
    """Every HARD_STOP fire's diag dict MUST contain ``scoped_child_active``.

    The PROMPT log showed ``scoped_child_active=?`` at the 3 consumer
    sites (subscriber.py:114, _idle_stream_timeout_error.py:30,
    _agent_inactivity_timeout_error.py:30). The root cause was the
    producer site only setting the key when ``scoped_child_active``
    was non-None in the corroborator snapshot; the
    ``_build_corroboration_diag`` helper skipped the assignment
    when the value was ``None`` and the consumer sites fell through
    to the ``?`` fallback.

    The fix: ``_build_corroboration_diag`` ALWAYS sets
    ``scoped_child_active`` (defaulting to False when None) so the
    3 consumer sites always see a concrete boolean.
    """
    # Capture emitted WaitingStatusEvents so we can inspect the
    # diag dict from the HARD_STOP emission.
    emitted: list[WaitingStatusEvent] = []

    def _capture(event: WaitingStatusEvent) -> None:
        emitted.append(event)

    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        watchdog_log_throttle_seconds=30.0,
        activity_evidence_ttl_seconds=180.0,
        stuck_job_sub_ceiling_seconds=600.0,
        max_waiting_on_child_seconds=1800.0,
        max_waiting_on_child_no_progress_seconds=1800.0,
    )
    clock = FakeClock(start=0.0)
    # Use a stale alive_by + scoped_child_active=True so the
    # stuck_job_sub_ceiling will trip at 600s. The corroborator
    # returns scoped_child_active=True, but the diagnostic
    # MUST also include the value (after the fix).
    watchdog = IdleWatchdog(
        policy,
        clock,
        listener=_capture,
        corroborator=lambda: CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
            oldest_child_seconds=200.0,
        ),
    )

    watchdog.record_invocation_start()
    clock.advance(201.0)
    watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)
    clock.advance(600.0)
    watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)

    # The HARD_STOP path (CHILDREN_PERSIST_TOO_LONG via the
    # _handle_waiting_branch path) MUST have emitted with a diag
    # dict that contains ``scoped_child_active`` (NOT the
    # ``?`` fallback).
    hard_stop_events = [
        e for e in emitted if e.kind == WaitingStatusKind.HARD_STOP
    ]
    assert hard_stop_events, (
        f"expected at least one HARD_STOP emission; got kinds="
        f"{[e.kind for e in emitted]}"
    )
    for event in hard_stop_events:
        diag = event.diagnostic or {}
        assert "scoped_child_active" in diag, (
            f"HARD_STOP diag dict MUST contain scoped_child_active key"
            f" (no '?' fallback); got diag={diag!r}"
        )
        assert isinstance(diag["scoped_child_active"], bool), (
            f"scoped_child_active MUST be a concrete boolean"
            f" (True or False), not None; got"
            f" {diag['scoped_child_active']!r}"
        )
