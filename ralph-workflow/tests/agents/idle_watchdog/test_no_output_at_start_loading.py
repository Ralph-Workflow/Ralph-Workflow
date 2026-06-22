"""Pin: NO_OUTPUT_AT_START LOADING-window false-positive fix.

The PROMPT log shows the watchdog fires ``NO_OUTPUT_AT_START`` at
``idle_elapsed=30.1s`` for a recently-launched agent. The agent is in
the first seconds of life: it may be loading the prompt, dispatching a
subagent, or otherwise pre-first-output. The dumb-kill floor
(``no_progress_quiet_minimum_invocation_seconds``, default 120 s) is
already enforced for ``NO_PROGRESS_QUIET`` in
``IdleWatchdog._is_no_progress_quiet`` but it is NOT consulted inside
``_evaluate_no_output_at_start`` -- so a recently-launched agent is
killed at the 30 s short ceiling before the WAITING_ON_CHILD
deferral can take over.

The fix: lift the dumb-kill floor guard into
``_evaluate_no_output_at_start`` so a recently-launched agent
(``invocation_elapsed_seconds < no_progress_quiet_minimum_invocation_seconds``)
is NOT killed by ``NO_OUTPUT_AT_START`` regardless of liveness state.

This test drives ``IdleWatchdog.evaluate()`` with:

  - ``no_output_at_start_seconds=30.0`` (the short ceiling)
  - ``no_progress_quiet_minimum_invocation_seconds=120.0`` (the floor)
  - invocation elapsed = 60.0 s (under the floor)
  - classify_quiet returns ACTIVE
  - channel_evidence_active returns False
  - corroboration.alive_by = ``OS_DESCENDANT_ONLY_STALE_PROGRESS``
    (a fresh dispatched-but-not-yet-active subagent: process
    tree descendant exists but no progress/heartbeat yet)

Asserts ``evaluate()`` returns ``WatchdogVerdict.CONTINUE``, NOT FIRE.
Pre-fix the floor is not consulted in ``_evaluate_no_output_at_start``
so the verdict is FIRE.

All tests use FakeClock; no real sleep, no real subprocess, no real
network.
"""

from __future__ import annotations

import typing
from dataclasses import dataclass

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WaitingCorroborator,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.idle_watchdog.corroboration_snapshot import CorroborationSnapshot
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
    """Returns a fixed CorroborationSnapshot with alive_by set."""

    def __init__(self, alive_by: AliveBy | None) -> None:
        self._alive_by = alive_by

    def __call__(self) -> CorroborationSnapshot:
        return CorroborationSnapshot(alive_by=self._alive_by)


def _active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def _make_watchdog(
    *,
    invocation_floor: float = 120.0,
    no_output_at_start: float = 30.0,
    alive_by: AliveBy | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=no_output_at_start,
        no_progress_quiet_seconds=None,
        no_progress_quiet_minimum_invocation_seconds=invocation_floor,
        activity_evidence_ttl_seconds=180.0,
    )
    return (
        IdleWatchdog(
            policy,
            clock,
            corroborator=cast(WaitingCorroborator, _StubCorroborator(alive_by)),
            process_monitor=_NoProcessMonitor(),
        ),
        clock,
    )


def cast(tp: type[object], obj: object) -> object:
    """Local cast helper to avoid shadowing typing.cast at module scope."""
    return typing.cast("type[object]", obj)


def test_no_output_at_start_defers_within_dumb_kill_floor() -> None:
    """NO_OUTPUT_AT_START MUST defer when invocation_elapsed_seconds is
    under the dumb-kill floor, regardless of alive_by.

    Drives evaluate() at invocation elapsed = 60 s with the floor at
    120 s. The corroborator reports
    ``OS_DESCENDANT_ONLY_STALE_PROGRESS`` (a wedged-startup signal:
    process tree descendant exists but no progress/heartbeat yet).
    Pre-fix this returns FIRE; post-fix it returns CONTINUE.
    """
    wd, clock = _make_watchdog(
        invocation_floor=120.0,
        no_output_at_start=30.0,
        alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
    )
    wd.record_invocation_start()

    # Advance to 60 s, which is:
    #   - past the 30 s no_output_at_start threshold (so the
    #     short ceiling WOULD fire pre-fix)
    #   - under the 120 s dumb-kill floor (so the floor MUST defer
    #     post-fix)
    clock.advance(60.0)

    verdict = wd.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"NO_OUTPUT_AT_START MUST defer within the dumb-kill floor"
        f" (invocation_elapsed=60s, floor=120s); got {verdict}"
    )


def test_no_output_at_start_still_fires_after_floor_elapsed() -> None:
    """NO_OUTPUT_AT_START MUST still fire once invocation_elapsed_seconds
    crosses the floor AND the short ceiling is hit.

    The dumb-kill floor only suppresses the short kill during the
    load window; past the floor the 30 s short ceiling is the correct
    bound for a never-launched agent.
    """
    wd, clock = _make_watchdog(
        invocation_floor=120.0,
        no_output_at_start=30.0,
        alive_by=None,
    )
    wd.record_invocation_start()
    # Advance to 150 s: past the floor and past the short ceiling.
    clock.advance(150.0)

    verdict = wd.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.FIRE, (
        f"NO_OUTPUT_AT_START MUST fire after the dumb-kill floor"
        f" elapses; got {verdict}"
    )
    assert wd.last_fire_reason == WatchdogFireReason.NO_OUTPUT_AT_START
