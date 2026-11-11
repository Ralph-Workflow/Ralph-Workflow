from __future__ import annotations

from ralph.agents.execution_state import AgentExecutionState, OpenCodeExecutionStrategy
from ralph.agents.idle_watchdog import (
    CorroborationSnapshot,
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.process.liveness import FakeLivenessProbe


class TestStuckChildPromptLogScenario:
    def test_os_descendant_only_fires_no_progress_ceiling_not_full_ceiling(self) -> None:
        full_ceiling = 100.0
        no_progress_ceiling = 20.0

        def _os_descendant_corroborator() -> CorroborationSnapshot:
            return CorroborationSnapshot(
                alive_by="os_descendant_only_stale_progress",
                scoped_child_active=True,
            )

        config = TimeoutPolicy(
            idle_timeout_seconds=1.0,
            drain_window_seconds=0.0,
            max_waiting_on_child_seconds=full_ceiling,
            suspect_waiting_on_child_seconds=None,
            waiting_status_interval_seconds=100.0,
            max_waiting_on_child_no_progress_seconds=no_progress_ceiling,
            stuck_job_sub_ceiling_seconds=None,
            os_descendant_only_ceiling_seconds=None,
            no_progress_quiet_seconds=None,
        )
        clock = FakeClock(start=0.0)
        watchdog = IdleWatchdog(config, clock, corroborator=_os_descendant_corroborator)

        strategy = OpenCodeExecutionStrategy()
        probe = FakeLivenessProbe(active=False)

        class _DescendantHandle:
            def has_live_descendants(self) -> bool:
                return True

        handle = _DescendantHandle()

        clock.advance(1.5)

        def classify_quiet() -> bool:
            return strategy.classify_quiet(handle, probe)

        result = watchdog.evaluate(classify_quiet=classify_quiet)
        assert result == WatchdogVerdict.WAITING_ON_CHILD, (
            f"Expected WAITING_ON_CHILD at entry; got {result!r}"
        )

        clock.advance(no_progress_ceiling - 2.0)
        result = watchdog.evaluate(classify_quiet=classify_quiet)
        assert result == WatchdogVerdict.WAITING_ON_CHILD, (
            f"Expected WAITING_ON_CHILD at t={no_progress_ceiling - 0.5}s; got {result!r}"
        )

        clock.advance(3.0)
        result = watchdog.evaluate(classify_quiet=classify_quiet)
        assert result == WatchdogVerdict.FIRE, (
            f"Expected FIRE at no-progress ceiling ({no_progress_ceiling}s); got {result!r}. "
            f"The watchdog must NOT wait for the full ceiling ({full_ceiling}s)."
        )
        assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    def test_classify_quiet_with_no_scope_returns_waiting_when_os_descendants_present(
        self,
    ) -> None:
        strategy = OpenCodeExecutionStrategy()
        probe = FakeLivenessProbe(active=False)

        class _DescendantHandle:
            def has_live_descendants(self) -> bool:
                return True

        handle = _DescendantHandle()
        result = strategy.classify_quiet(handle, probe)

        assert result == AgentExecutionState.WAITING_ON_CHILD, (
            f"No scoped evidence + OS descendants must yield WAITING_ON_CHILD; got {result!r}. "
            "This is the entry condition for the os_descendant_only_stale_progress corroboration."
        )
