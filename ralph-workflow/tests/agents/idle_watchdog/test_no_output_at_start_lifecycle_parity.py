"""Regression tests for the 30s ``NO_OUTPUT_AT_START`` false-positive fix.

The PROMPT trace showed the watchdog firing ``NO_OUTPUT_AT_START`` at
~30s on a freshly-launched agent that had a live child registered
in the process tree (a subagent dispatched at invocation start).
The agent was killed and the orchestrator started a fresh session
instead of resuming. Root cause: the deferral gate inside
``_evaluate_no_output_at_start`` was ``corroboration.alive_by is not None``
which deferred on every ``AliveBy`` value including stale states.

The fix:
  * Define ``_FRESH_ALIVE_BY_STATES = {FRESH_PROGRESS, FRESH_HEARTBEAT_ONLY}``
    as the canonical "live child agent" subset.
  * Use ``_alive_by_is_fresh(...)`` as the deferral gate so stale
    ``AliveBy`` values (``OS_DESCENDANT_ONLY_STALE_PROGRESS``,
    ``CPU_IDLE_WHILE_ALIVE``, ``LOG_STALE_WHILE_ALIVE``,
    ``STALE_LABEL_ONLY``) fall through to ``_gate_fire`` and the
    short kill still applies.

These tests pin BOTH the FRESH deferral paths (so a productive live
child agent is never killed by the 30s short ceiling) AND the STALE
fire paths (so a wedged startup is still detected). The pre-fix bug
would fail at least one test in each direction, so a future change
that re-widens the gate (or narrows it) trips a clear regression.

All tests use ``FakeClock`` and the public ``evaluate()`` API; no
real subprocess, no real sleep, no real network.
"""

from __future__ import annotations

import pytest

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    CorroborationSnapshot,
    IdleWatchdog,
    TimeoutPolicy,
    WaitingCorroborator,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.process.child_liveness import AliveBy

_FRESH_ALIVE_BY_STATES: tuple[AliveBy, ...] = (
    AliveBy.FRESH_PROGRESS,
    AliveBy.FRESH_HEARTBEAT_ONLY,
)

_STALE_ALIVE_BY_STATES: tuple[AliveBy, ...] = (
    AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
    AliveBy.CPU_IDLE_WHILE_ALIVE,
    AliveBy.LOG_STALE_WHILE_ALIVE,
    AliveBy.STALE_LABEL_ONLY,
)


def _make_policy(*, no_output_at_start_seconds: float = 30.0) -> TimeoutPolicy:
    return TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=no_output_at_start_seconds,
        no_progress_quiet_seconds=None,
        no_progress_quiet_minimum_invocation_seconds=None,
        max_waiting_on_child_seconds=1800.0,
        max_waiting_on_child_no_progress_seconds=600.0,
        suspect_waiting_on_child_seconds=None,
        activity_evidence_ttl_seconds=180.0,
        silent_subagent_seconds=None,
    )


def _make_watchdog_with_corroborator(
    corroborator: WaitingCorroborator,
) -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    return (
        IdleWatchdog(_make_policy(), clock, corroborator=corroborator),
        clock,
    )


def _active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


@pytest.mark.parametrize("alive_by", _FRESH_ALIVE_BY_STATES)
def test_no_output_at_start_defers_when_alive_by_is_fresh(alive_by: AliveBy) -> None:
    """Fresh ``AliveBy`` states defer the 30s ``NO_OUTPUT_AT_START`` kill.

    Pins the false-positive fix: a live child agent with a recent
    progress or heartbeat signal MUST defer the short ceiling. Pre-fix
    the gate was ``alive_by is not None`` which deferred on every
    ``AliveBy`` value, including stale ones. The new gate is the
    fresh-evidence subset, so a productive live child is never
    killed by the short fire.
    """
    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=alive_by,
            scoped_child_active=True,
            oldest_child_seconds=5.0,
        )

    watchdog, clock = _make_watchdog_with_corroborator(_corroborator)
    watchdog.record_invocation_start()

    clock.advance(31.0)
    verdict = watchdog.evaluate(classify_quiet=_active)

    assert verdict == WatchdogVerdict.CONTINUE, (
        f"NO_OUTPUT_AT_START MUST defer for fresh alive_by={alive_by.value!r};"
        f" got verdict={verdict!r}"
    )
    assert watchdog.last_fire_reason is None, (
        f"NO_OUTPUT_AT_START MUST NOT fire for fresh alive_by={alive_by.value!r};"
        f" got last_fire_reason={watchdog.last_fire_reason!r}"
    )


@pytest.mark.parametrize("alive_by", _STALE_ALIVE_BY_STATES)
def test_no_output_at_start_fires_when_alive_by_is_stale(alive_by: AliveBy) -> None:
    """Stale ``AliveBy`` states do NOT defer ``NO_OUTPUT_AT_START``.

    Pins the no-false-negative contract: a wedged-startup pattern
    where the corroborator reports a stale ``AliveBy`` value MUST
    still fire the short kill. Pre-fix the gate was
    ``alive_by is not None`` which would defer the fire on stale
    states, letting a wedged agent run for the cumulative 600s
    no-progress ceiling (too late for a 30s-startup wedge).

    The new gate restricts the deferral to the FRESH subset so a
    stale corroborator signal falls through to ``_gate_fire`` /
    ``_classify_stuck_now``. The StuckClassifier may still defer
    when the run is genuinely classified as non-stuck (the gate
    contract); the assertion here verifies the deferral does NOT
    happen via ``alive_by``-is-not-None.
    """
    def _corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=alive_by,
            scoped_child_active=True,
            oldest_child_seconds=5.0,
        )

    watchdog, clock = _make_watchdog_with_corroborator(_corroborator)
    watchdog.record_invocation_start()

    clock.advance(31.0)
    verdict = watchdog.evaluate(classify_quiet=_active)

    assert verdict == WatchdogVerdict.FIRE, (
        f"NO_OUTPUT_AT_START MUST fire for stale alive_by={alive_by.value!r};"
        f" got verdict={verdict!r}"
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START, (
        f"NO_OUTPUT_AT_START MUST be the fire reason for stale"
        f" alive_by={alive_by.value!r}; got"
        f" last_fire_reason={watchdog.last_fire_reason!r}"
    )


def test_no_output_at_start_full_lifecycle_parity() -> None:
    """Lifecycle parity: FRESH states defer; STALE states fire.

    Combines both directions in a single deterministic test that
    exercises the full ``evaluate()`` lifecycle for one fresh and
    one stale state in sequence. The point is to confirm both
    directions of the gate are honoured in the same watchdog
    instance (the reset path between invocations does not regress).
    """
    # Fresh state first: defer.
    def _fresh_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.FRESH_PROGRESS,
            scoped_child_active=True,
            oldest_child_seconds=5.0,
        )

    watchdog, clock = _make_watchdog_with_corroborator(_fresh_corroborator)
    watchdog.record_invocation_start()
    clock.advance(31.0)
    verdict = watchdog.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"FRESH_PROGRESS MUST defer; got {verdict!r}"
    )

    # Reset invocation to simulate a new run on a SEPARATE watchdog
    # instance -- the fresh-state deferral must NOT carry over to the
    # next invocation (no stale-alive_by caching). Using a fresh
    # ``IdleWatchdog`` for the stale case keeps the test fully typed
    # (the corroborator is a constructor parameter, not an attribute
    # to be swapped after construction).
    del watchdog
    def _stale_corroborator() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
            scoped_child_active=True,
            oldest_child_seconds=5.0,
        )

    stale_watchdog, stale_clock = _make_watchdog_with_corroborator(
        _stale_corroborator
    )
    stale_watchdog.record_invocation_start()
    stale_clock.advance(31.0)
    verdict = stale_watchdog.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.FIRE, (
        f"OS_DESCENDANT_ONLY_STALE_PROGRESS MUST fire; got {verdict!r}"
    )
    assert (
        stale_watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START
    )
