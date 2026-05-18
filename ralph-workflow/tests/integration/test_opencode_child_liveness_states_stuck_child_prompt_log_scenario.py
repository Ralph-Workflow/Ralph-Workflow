"""End-to-end regression scenarios for OpenCode child liveness classification.

Three operator-critical scenarios tested as black-box via the public execution strategy API:

1. Child exited cleanly — terminal ack seen → TERMINAL_COMPLETE, no indefinite waiting.
2. Child hung — process label exists but progress/heartbeat expired → RESUMABLE_CONTINUE
   (stale process existence alone must NOT hold WAITING_ON_CHILD open).
3. Child still working — progress lease keeps renewing → WAITING_ON_CHILD maintained.

Also includes post-exit path integration tests that drive check_process_result and
PostExitWatchdog with FakeClock to validate the planned end-to-end behaviors.
"""

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
    """Regression for wt-97 Bug 1: prompt logs show ceiling=1800s with alive_by=os_descendant.

    When a WAITING_ON_CHILD run has no scoped Ralph child evidence but OS descendants
    are present (alive_by=os_descendant_only_stale_progress), the no-progress ceiling
    must fire — not the full hard ceiling — so the watchdog times out promptly.

    This mirrors the prompt's stuck-agent log where cumulative reached 600s+ while
    ceiling remained 1800.0s. After the fix, the no-progress ceiling (configured to
    600s in production; smaller values used here for test speed) must fire instead.
    """

    def test_os_descendant_only_fires_no_progress_ceiling_not_full_ceiling(self) -> None:
        """With OS-descendant-only evidence, the watchdog fires at the no-progress ceiling.

        No registered Ralph children (empty registry), OS descendants present.
        classify_quiet returns WAITING_ON_CHILD. The corroborator produces
        alive_by=os_descendant_only_stale_progress. The effective ceiling must be
        the shorter no-progress ceiling, not the full ceiling.
        """
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
        )
        clock = FakeClock(start=0.0)
        watchdog = IdleWatchdog(config, clock, corroborator=_os_descendant_corroborator)

        strategy = OpenCodeExecutionStrategy()
        probe = FakeLivenessProbe(active=False)

        class _DescendantHandle:
            def has_live_descendants(self) -> bool:
                return True

        handle = _DescendantHandle()

        # Enter WAITING: no scoped Ralph evidence + OS descendants → WAITING_ON_CHILD.
        clock.advance(1.5)
        def classify_quiet() -> bool:
            return strategy.classify_quiet(handle, probe)

        result = watchdog.evaluate(classify_quiet=classify_quiet)
        assert result == WatchdogVerdict.WAITING_ON_CHILD, (
            f"Expected WAITING_ON_CHILD at entry; got {result!r}"
        )

        # Advance to just under the no-progress ceiling: must still be WAITING.
        clock.advance(no_progress_ceiling - 2.0)
        result = watchdog.evaluate(classify_quiet=classify_quiet)
        assert result == WatchdogVerdict.WAITING_ON_CHILD, (
            f"Expected WAITING_ON_CHILD at t={no_progress_ceiling - 0.5}s; got {result!r}"
        )

        # Advance past the no-progress ceiling: must FIRE, not wait for the full ceiling.
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
        """Without registered children, classify_quiet returns WAITING_ON_CHILD for OS descendants.

        This is the trigger condition for the prompt's stuck-child scenario:
        the classify_quiet falls back to OS descendant presence when no scoped
        Ralph child evidence exists, yielding WAITING_ON_CHILD. The corroborator
        then sees alive_by=os_descendant_only_stale_progress and the no-progress
        ceiling must fire (tested in the sibling test above).
        """
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
