"""A long native OpenCode ``task`` must not get the parent killed.

Reproduces the measured 1.18.25 wire shape: the parent emits ``step_start``,
then stays silent until the child finishes (the ``task`` ``tool_use`` frame
is buffered until completion). Under the default timeout policy that silence
used to fire ``NO_PROGRESS_QUIET`` at 240 s (no OS descendants) or
``CHILDREN_PERSIST_TOO_LONG`` at 600 s (child shelling out) -- while real
subagents run 10-15 minutes. The child's work is visible in OpenCode's own
session store; fed through the store probe it must keep the parent alive
for as long as the child keeps working, and no longer.
"""

from __future__ import annotations

import pytest

from ralph.agents.activity import AgentActivityKind
from ralph.agents.execution_state import OpenCodeExecutionStrategy
from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy
from ralph.agents.idle_watchdog.corroboration_snapshot import CorroborationSnapshot
from ralph.agents.idle_watchdog.watchdog_fire_reason import WatchdogFireReason
from ralph.agents.idle_watchdog.watchdog_verdict import WatchdogVerdict
from ralph.agents.invoke.opencode_subagent_sessions import (
    OpenCodeChildPart,
    OpenCodeSubagentSessionProbe,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.process.child_liveness import ChildLivenessRegistry, classify_child_snapshot
from ralph.process.liveness import FakeLivenessProbe
from ralph.timeout_defaults import (
    CHILD_EXIT_RECONCILE_SECONDS,
    CHILD_HEARTBEAT_TTL_SECONDS,
    CHILD_PROGRESS_TTL_SECONDS,
    CHILD_STALE_LABEL_TTL_SECONDS,
    IDLE_TIMEOUT_SECONDS,
    MAX_SESSION_SECONDS,
)
from tests.fake_handle import _FakeHandle

_TICK_SECONDS = 10.0


class _WorkingChild:
    """A child session that writes one store part per poll until ``active_until``."""

    def __init__(self, clock: FakeClock, active_until: float) -> None:
        self._clock = clock
        self._active_until = active_until
        self._n = 0

    def fetch(self, parent_session_id: str, since_ms: int) -> list[OpenCodeChildPart]:
        del parent_session_id, since_ms
        now = self._clock.monotonic()
        if now > self._active_until:
            return []
        self._n += 1
        return [
            OpenCodeChildPart(
                child_session_id="ses_child",
                agent="Sisyphus-Junior",
                title="Implement S7",
                part_id=f"prt_{self._n}",
                kind="tool:ralph_read_file",
                time_updated_ms=int(now * 1000),
            )
        ]

    def close(self) -> None:
        return None


def _run(
    *,
    task_seconds: float,
    child_active_seconds: float | None,
    os_descendants: bool,
) -> tuple[float | None, WatchdogFireReason | None]:
    clock = FakeClock(start=1000.0)
    registry = ChildLivenessRegistry(
        progress_ttl=CHILD_PROGRESS_TTL_SECONDS,
        heartbeat_ttl=CHILD_HEARTBEAT_TTL_SECONDS,
        stale_label_ttl=CHILD_STALE_LABEL_TTL_SECONDS,
        exit_reconcile=CHILD_EXIT_RECONCILE_SECONDS,
        now=clock.monotonic,
    )
    strategy = OpenCodeExecutionStrategy(label_scope=None, registry=registry)
    handle = _FakeHandle(has_descendants=os_descendants)
    liveness = FakeLivenessProbe(active=False)

    def corroborate() -> CorroborationSnapshot:
        verdict = classify_child_snapshot(registry.snapshot(""), has_os_descendants=os_descendants)
        return CorroborationSnapshot(
            scoped_child_active=os_descendants,
            scoped_child_count=1 if os_descendants else 0,
            alive_by=verdict.alive_by,
        )

    watchdog = IdleWatchdog(
        TimeoutPolicy(
            idle_timeout_seconds=IDLE_TIMEOUT_SECONDS, max_session_seconds=MAX_SESSION_SECONDS
        ),
        clock,
        corroborator=corroborate,
    )
    watchdog.record_invocation_start()

    def feed(line: str) -> None:
        signal = strategy.classify_activity_line(line)
        assert signal is not None
        if signal.kind == AgentActivityKind.LIFECYCLE:
            watchdog.record_lifecycle_activity()
        else:
            watchdog.record_activity()
        strategy.observe_line(line)

    feed('{"type":"step_start","sessionID":"ses_parent","part":{"type":"step-start"}}')
    clock.advance(1.0)
    feed('{"type":"text","sessionID":"ses_parent","part":{"type":"text","text":"Delegating"}}')
    clock.advance(1.0)
    feed('{"type":"step_start","sessionID":"ses_parent","part":{"type":"step-start"}}')

    probe: OpenCodeSubagentSessionProbe | None = None
    if child_active_seconds is not None:

        def _sink(summary: str) -> None:
            watchdog.record_subagent_work(description=summary)
            watchdog.record_activity()

        probe = OpenCodeSubagentSessionProbe(
            source=_WorkingChild(clock, clock.monotonic() + child_active_seconds),
            parent_session_id=lambda: "ses_parent",
            subagent_sink=_sink,
            child_progress_sink=strategy.record_native_child_progress,
            monotonic=clock.monotonic,
            wall_clock_ms=lambda: 0,
        )

    started = clock.monotonic()
    while clock.monotonic() - started < task_seconds:
        clock.advance(_TICK_SECONDS)
        if probe is not None:
            probe.poll()
        verdict = watchdog.evaluate(
            classify_quiet=lambda: strategy.classify_quiet(handle, liveness)
        )
        if verdict == WatchdogVerdict.FIRE:
            return clock.monotonic() - started, watchdog.last_fire_reason
    return None, None


@pytest.mark.parametrize("os_descendants", [False, True])
def test_silent_parent_with_buffered_task_used_to_die_within_ten_minutes(
    os_descendants: bool,
) -> None:
    fired_at, reason = _run(
        task_seconds=1500.0, child_active_seconds=None, os_descendants=os_descendants
    )
    assert fired_at is not None and fired_at <= 600.0
    assert reason in {
        WatchdogFireReason.NO_PROGRESS_QUIET,
        WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
    }


@pytest.mark.parametrize("os_descendants", [False, True])
def test_working_child_keeps_the_parent_alive_for_a_25_minute_task(os_descendants: bool) -> None:
    fired_at, _ = _run(
        task_seconds=1500.0, child_active_seconds=1500.0, os_descendants=os_descendants
    )
    assert fired_at is None


@pytest.mark.parametrize("os_descendants", [False, True])
def test_a_child_that_stops_writing_still_reaches_the_ceilings(os_descendants: bool) -> None:
    fired_at, reason = _run(
        task_seconds=2400.0, child_active_seconds=600.0, os_descendants=os_descendants
    )
    assert fired_at is not None
    assert 600.0 < fired_at <= 600.0 + 700.0, (
        "the kill lands after the child went quiet, within the standard ceilings"
    )
    assert reason in {
        WatchdogFireReason.NO_PROGRESS_QUIET,
        WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
    }
