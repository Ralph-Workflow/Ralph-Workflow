"""Pin: heartbeat-only ceiling for heartbeat-only stuck jobs.

The PROMPT log shows heartbeat-only subagents (``AliveBy.FRESH_HEARTBEAT_ONLY``
-- alive per the corroborator but no first-party progress) running for the
full cumulative ``CHILDREN_PERSIST_TOO_LONG`` ceiling (default 600s) without
being killed, even though they emit heartbeats but no real work.

The fix: a dedicated heartbeat-only ceiling in
``TimeoutPolicy.no_progress_quiet_heartbeat_ceiling_seconds`` (default 240s;
must be <= ``no_progress_quiet_seconds`` per the cross-field validator)
that fires ``NO_PROGRESS_QUIET`` when the corroborator reports
``AliveBy.FRESH_HEARTBEAT_ONLY`` AND ``invocation_elapsed_seconds`` >=
the ceiling. Without this branch, ``_is_no_progress_quiet`` short-circuits
when ``alive_by is not None`` (the wt-012 gate refinement), and
``_evaluate_strictly_stuck`` only fires for stale ``alive_by`` values
(``OS_DESCENDANT_ONLY_STALE_PROGRESS``, ``CPU_IDLE_WHILE_ALIVE``,
``LOG_STALE_WHILE_ALIVE``), so a heartbeat-only subagent bypasses BOTH
paths.

These tests pin the five key branches:

1. Heartbeat-only trip: ``FRESH_HEARTBEAT_ONLY`` + elapsed >=
   heartbeat-only ceiling -> ``NO_PROGRESS_QUIET`` FIRE.
2. Heartbeat ceiling does NOT trip before its threshold.
3. Heartbeat ceiling fires BEFORE the dumb-kill ceiling when
   ``heartbeat_ceiling < no_progress_quiet_seconds`` (the operator-knob
   behavior; the whole point of the field).
4. ``FRESH_PROGRESS`` (real progress, not just heartbeat) continues to
   defer indefinitely (not killed by the heartbeat-only branch).
5. ``None`` disables the heartbeat-only ceiling.

All tests use ``FakeClock``; no real ``time.sleep``, no real subprocess,
no real network. Default test layer: ``unit`` (NOT ``subprocess_e2e``
and NOT ``smoke``).
"""

from __future__ import annotations

import typing
from dataclasses import dataclass

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WaitingCorroborator,
)
from ralph.agents.idle_watchdog.corroboration_snapshot import CorroborationSnapshot
from ralph.agents.idle_watchdog.watchdog_fire_reason import WatchdogFireReason
from ralph.agents.idle_watchdog.watchdog_verdict import WatchdogVerdict
from ralph.agents.timeout_clock import FakeClock
from ralph.process.child_liveness import AliveBy


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


class _StubCorroborator:
    def __init__(self, alive_by: AliveBy | None) -> None:
        self._alive_by = alive_by

    def __call__(self) -> CorroborationSnapshot:
        return CorroborationSnapshot(alive_by=self._alive_by)


def _waiting() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


def _active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def _make_watchdog(
    *,
    heartbeat_ceiling_seconds: float | None = 10.0,
    no_progress_quiet_seconds: float | None = 10.0,
    no_progress_quiet_minimum_invocation_seconds: float | None = 10.0,
    alive_by: AliveBy | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=None,
        no_progress_quiet_seconds=no_progress_quiet_seconds,
        no_progress_quiet_minimum_invocation_seconds=(
            no_progress_quiet_minimum_invocation_seconds
        ),
        no_progress_quiet_heartbeat_ceiling_seconds=heartbeat_ceiling_seconds,
        activity_evidence_ttl_seconds=180.0,
        max_waiting_on_child_seconds=1800.0,
        max_waiting_on_child_no_progress_seconds=600.0,
        suspect_waiting_on_child_seconds=None,
        watchdog_log_throttle_seconds=30.0,
        watchdog_subagent_progress_interval_seconds=30.0,
    )
    return (
        IdleWatchdog(
            policy,
            clock,
            corroborator=typing.cast("WaitingCorroborator", _StubCorroborator(alive_by)),
            process_monitor=_NoProcessMonitor(),
        ),
        clock,
    )


def test_heartbeat_only_trip() -> None:
    """Heartbeat-only subagent trips NO_PROGRESS_QUIET once ceiling elapses.

    Pre-fix: ``_is_no_progress_quiet`` short-circuits when
    ``alive_by is not None`` so a heartbeat-only subagent would defer
    until the cumulative 600s ``CHILDREN_PERSIST_TOO_LONG`` ceiling.
    Post-fix: the dedicated heartbeat-only ceiling (10s) trips
    ``NO_PROGRESS_QUIET`` once ``invocation_elapsed_seconds >= 10s``
    AND the dumb-kill floor (10s) has elapsed (so the floor guard does
    not defer).

    Setup: heartbeat_ceiling = 10s, no_progress_quiet_seconds = 5s
    (so the outer ``invocation_elapsed >= no_progress_quiet_seconds``
    check passes when the heartbeat ceiling elapses at 10s+), floor =
    10s, advance by 11s -> FIRE with reason NO_PROGRESS_QUIET.
    """
    wd, clock = _make_watchdog(
        heartbeat_ceiling_seconds=10.0,
        no_progress_quiet_seconds=10.0,
        no_progress_quiet_minimum_invocation_seconds=10.0,
        alive_by=AliveBy.FRESH_HEARTBEAT_ONLY,
    )
    wd.record_invocation_start()
    clock.advance(11.0)
    verdict = wd.evaluate(classify_quiet=_waiting)
    assert verdict == WatchdogVerdict.FIRE, (
        f"NO_PROGRESS_QUIET MUST fire at invocation elapsed = 11s with"
        f" alive_by=FRESH_HEARTBEAT_ONLY and heartbeat_ceiling=10s;"
        f" got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.NO_PROGRESS_QUIET, (
        f"expected WatchdogFireReason.NO_PROGRESS_QUIET;"
        f" got {wd.last_fire_reason}"
    )


def test_heartbeat_ceiling_does_not_trip_before_threshold() -> None:
    """Heartbeat ceiling MUST NOT fire before its threshold elapses.

    Verifies the ceiling semantics: at 9s with the heartbeat ceiling
    at 10s the watchdog returns CONTINUE (the ceiling has not elapsed).
    """
    wd, clock = _make_watchdog(
        heartbeat_ceiling_seconds=10.0,
        no_progress_quiet_seconds=10.0,
        no_progress_quiet_minimum_invocation_seconds=10.0,
        alive_by=AliveBy.FRESH_HEARTBEAT_ONLY,
    )
    wd.record_invocation_start()
    clock.advance(9.0)
    verdict = wd.evaluate(classify_quiet=_waiting)
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"Heartbeat-only ceiling MUST NOT fire before its threshold"
        f" elapses (invocation_elapsed=9s, ceiling=10s); got {verdict}"
    )


def test_fresh_progress_deferral_preserved() -> None:
    """FRESH_PROGRESS (real progress) continues to defer indefinitely.

    The heartbeat-only branch is a heartbeat-only branch: it MUST NOT
    kill subagents that report ``AliveBy.FRESH_PROGRESS`` (real
    progress, not just heartbeats). At 100s with the heartbeat ceiling
    at 10s and ``alive_by=FRESH_PROGRESS``, the verdict is NOT FIRE
    because the branch only fires for ``FRESH_HEARTBEAT_ONLY``. The
    WAITING_ON_CHILD classifier is used so the test exercises the
    same waiting-branch path the production bug lives on; otherwise
    the test would pass on the ACTIVE path and not catch the
    no_progress_quiet_seconds short-circuit regression. The verdict
    is either ``CONTINUE`` (dumb-kill path defers via the
    alive_by is not None short-circuit when heartbeat_ceiling is
    not None) or ``WAITING_ON_CHILD`` (waiting-branch deferral
    returned by ``_handle_waiting_branch``); either outcome is a
    non-fire continuation.
    """
    wd, clock = _make_watchdog(
        heartbeat_ceiling_seconds=10.0,
        no_progress_quiet_seconds=10.0,
        no_progress_quiet_minimum_invocation_seconds=10.0,
        alive_by=AliveBy.FRESH_PROGRESS,
    )
    wd.record_invocation_start()
    clock.advance(100.0)
    verdict = wd.evaluate(classify_quiet=_waiting)
    assert verdict != WatchdogVerdict.FIRE, (
        f"Heartbeat-only ceiling MUST NOT fire for FRESH_PROGRESS"
        f" (real progress, not heartbeat); got {verdict}"
    )


def test_heartbeat_ceiling_disabled_when_none() -> None:
    """When ``no_progress_quiet_heartbeat_ceiling_seconds`` is None the
    heartbeat-only ceiling is disabled and the watchdog does NOT
    fire.

    Operators can opt out by setting the field to ``None``. The
    default 240s is opt-in via ``[general]`` config (the
    ``agent_no_progress_quiet_heartbeat_ceiling_seconds`` field
    on ``GeneralConfig``, which inherits the constant
    ``NO_PROGRESS_QUIET_HEARTBEAT_CEILING_SECONDS`` from
    ``ralph/timeout_defaults.py``). The
    WAITING_ON_CHILD classifier is used so the test exercises the
    same waiting-branch path the production bug lives on; otherwise
    the test would pass on the ACTIVE path and not catch the
    no_progress_quiet_seconds short-circuit regression. The
    ``FRESH_HEARTBEAT_ONLY`` deferral still routes through
    ``_handle_waiting_branch`` (cumulative
    ``CHILDREN_PERSIST_TOO_LONG`` ceiling at 1800s, well above
    100s) so the verdict is either ``CONTINUE`` or
    ``WAITING_ON_CHILD`` (non-fire continuation).
    """
    wd, clock = _make_watchdog(
        heartbeat_ceiling_seconds=None,
        no_progress_quiet_seconds=10.0,
        no_progress_quiet_minimum_invocation_seconds=10.0,
        alive_by=AliveBy.FRESH_HEARTBEAT_ONLY,
    )
    wd.record_invocation_start()
    clock.advance(100.0)
    verdict = wd.evaluate(classify_quiet=_waiting)
    # Disabled: not FIRE because FRESH_HEARTBEAT_ONLY defers at the
    # alive_by short-circuit (no heartbeat branch to fire). The
    # watchdog falls back to the cumulative CHILDREN_PERSIST_TOO_LONG
    # ceiling at 1800s (well above 100s).
    assert verdict != WatchdogVerdict.FIRE, (
        f"Heartbeat-only ceiling MUST be disabled when the field is None;"
        f" got {verdict}"
    )


def test_heartbeat_only_ceiling_fires_before_dumb_kill_ceiling() -> None:
    """Heartbeat-only ceiling fires BEFORE the dumb-kill ceiling.

    This is the operator-knob behavior: when
    ``heartbeat_ceiling < no_progress_quiet_seconds``, the heartbeat
    ceiling trips ``NO_PROGRESS_QUIET`` earlier than the dumb-kill
    ceiling. The fix moves the heartbeat-only branch ABOVE the
    ``invocation_elapsed_seconds < no_progress_quiet_seconds``
    short-circuit so the heartbeat ceiling is consulted BEFORE the
    dumb-kill gate.

    Setup: heartbeat_ceiling = 60s, no_progress_quiet_seconds = 120s
    (so the heartbeat ceiling fires EARLIER than the dumb-kill
    ceiling; the cross-field validator enforces
    heartbeat_ceiling <= no_progress_quiet_seconds). Advance by 61s
    -> FIRE with reason NO_PROGRESS_QUIET. Pre-fix, this scenario
    deferred until the 120s dumb-kill ceiling (or the cumulative
    600s CHILDREN_PERSIST_TOO_LONG ceiling if
    no_progress_quiet_seconds was disabled).
    """
    wd, clock = _make_watchdog(
        heartbeat_ceiling_seconds=60.0,
        no_progress_quiet_seconds=120.0,
        no_progress_quiet_minimum_invocation_seconds=10.0,
        alive_by=AliveBy.FRESH_HEARTBEAT_ONLY,
    )
    wd.record_invocation_start()
    clock.advance(61.0)
    verdict = wd.evaluate(classify_quiet=_waiting)
    assert verdict == WatchdogVerdict.FIRE, (
        f"Heartbeat-only ceiling (60s) MUST fire BEFORE the dumb-kill"
        f" ceiling (120s) at invocation elapsed = 61s with"
        f" alive_by=FRESH_HEARTBEAT_ONLY; got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.NO_PROGRESS_QUIET, (
        f"expected WatchdogFireReason.NO_PROGRESS_QUIET;"
        f" got {wd.last_fire_reason}"
    )


def test_heartbeat_only_ceiling_respects_dumb_kill_floor() -> None:
    """The dumb-kill floor still protects recently-launched heartbeat-only agents.

    The heartbeat-only branch honors
    ``no_progress_quiet_minimum_invocation_seconds`` so a
    recently-launched agent doing real thinking work but emitting
    only heartbeats is not killed before the dumb-kill floor elapses.

    Setup: heartbeat_ceiling = 10s, floor = 30s, advance by 11s ->
    NOT FIRE because the floor (30s) has not elapsed yet. The
    ``FRESH_HEARTBEAT_ONLY`` deferral still routes through
    ``_handle_waiting_branch`` (cumulative ceiling at 1800s, well
    above 11s) so the verdict is either ``CONTINUE`` or
    ``WAITING_ON_CHILD`` (non-fire continuation).
    """
    wd, clock = _make_watchdog(
        heartbeat_ceiling_seconds=10.0,
        no_progress_quiet_seconds=120.0,
        no_progress_quiet_minimum_invocation_seconds=30.0,
        alive_by=AliveBy.FRESH_HEARTBEAT_ONLY,
    )
    wd.record_invocation_start()
    clock.advance(11.0)
    verdict = wd.evaluate(classify_quiet=_waiting)
    assert verdict != WatchdogVerdict.FIRE, (
        f"Heartbeat-only ceiling MUST NOT fire before the dumb-kill"
        f" floor elapses; got {verdict}"
    )


def test_heartbeat_only_trip_when_no_progress_quiet_seconds_disabled() -> None:
    """Heartbeat-only branch fires even when ``no_progress_quiet_seconds=None``.

    REGRESSION TEST for the analysis feedback runtime bug. The
    heartbeat-only ceiling is ORTHOGONAL to the dumb-kill ceiling;
    a ``None`` ``no_progress_quiet_seconds`` (operator disables the
    dumb-kill trip) MUST NOT also disable the heartbeat-only trip.

    Pre-fix: ``_evaluate_no_progress_quiet`` short-circuited when
    ``no_progress_quiet_seconds`` was ``None`` AND ``evaluate()``
    skipped calling ``_evaluate_no_progress_quiet`` when
    ``no_progress_quiet_seconds`` was ``None``. The combination
    meant a heartbeat-only subagent would run indefinitely even
    with a configured heartbeat ceiling.

    Post-fix: ``evaluate()`` calls ``_evaluate_no_progress_quiet``
    when EITHER ceiling is configured, and
    ``_evaluate_no_progress_quiet`` only short-circuits when BOTH
    ceilings are ``None``.

    Setup: heartbeat_ceiling = 10s, no_progress_quiet_seconds = None
    (dumb-kill disabled), alive_by=FRESH_HEARTBEAT_ONLY, advance by
    11s -> FIRE with reason NO_PROGRESS_QUIET. Pre-fix this returned
    ``verdict=continue, reason=None`` (the heartbeat branch never
    fired). Post-fix it returns ``verdict=FIRE, reason=NO_PROGRESS_QUIET``.
    """
    wd, clock = _make_watchdog(
        heartbeat_ceiling_seconds=10.0,
        no_progress_quiet_seconds=None,
        no_progress_quiet_minimum_invocation_seconds=None,
        alive_by=AliveBy.FRESH_HEARTBEAT_ONLY,
    )
    wd.record_invocation_start()
    clock.advance(11.0)
    verdict = wd.evaluate(classify_quiet=_waiting)
    assert verdict == WatchdogVerdict.FIRE, (
        f"Heartbeat-only branch MUST fire when "
        f"no_progress_quiet_seconds=None AND "
        f"no_progress_quiet_heartbeat_ceiling_seconds=10.0 AND "
        f"invocation_elapsed=11s with alive_by=FRESH_HEARTBEAT_ONLY;"
        f" got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.NO_PROGRESS_QUIET, (
        f"expected WatchdogFireReason.NO_PROGRESS_QUIET;"
        f" got {wd.last_fire_reason}"
    )

