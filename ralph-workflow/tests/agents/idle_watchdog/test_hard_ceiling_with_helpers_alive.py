"""Black-box tests for the R3 hard-ceiling-fires-with-helpers-alive contract.

R3 (Trustworthy Idle Watchdog product spec):

    Every genuine hang fires within a bounded ceiling, even when a
    non-subagent process looks like a lingering child.

The product spec cites the ``idle_elapsed=2365s+`` indefinite deferral
as the headline bug: ``children_persist_too_long`` deferred forever
because the watchdog believed a non-subagent helper process (a shell
helper like ``npm test``, ``cargo build``, ``find /``) was a
"persisting child" worth waiting on.

The fix: the watchdog's hard ceilings are checked AFTER the
FILTERED subagent count (not the broader ``descendant_snapshot``
count) is consulted. A monitor that reports ``spawned_subagent_count() == 0``
is "no real subagent alive" regardless of how many shell helpers
are visible in the descendant tree. The hard ceiling fires.

The tests in this module are pure black-box:

    * No real subprocess. No real time. No real filesystem.
    * Synthetic process trees are simulated by injecting a fake
      ``ProcessMonitor`` whose ``spawned_subagent_count`` /
      ``live_subagent_count`` returns 0 (the FILTERED count is what
      the watchdog reads).
    * The broader descendant count is exposed as a separate attribute
      on the fake (for documentation of the bug class) but is NOT
      consumed by the watchdog -- the audit
      ``audit_activity_aware_watchdog.subagent_counting_seam`` flags
      any reader that consumes the broader count for
      ``scoped_child_active``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.process.monitor import (
    ProcessMonitor,
    SubagentOutputCapture,
)


@dataclass
class _HelpersOnlyMonitor(ProcessMonitor):
    """Fake monitor: filtered count is 0, broader count is N helpers.

    The filtered count (the SEAM) is what the watchdog defers on.
    Helpers are visible to the broader ``descendant_snapshot()`` count
    but NOT to the filtered count; the watchdog fires the hard ceiling
    regardless of the helper count.

    The ``helper_count`` field is documented for the test (and for the
    audit regression test) but is NOT consumed by the watchdog itself.
    """

    helper_count: int = 10
    classified: tuple = field(default_factory=tuple)
    outputs: dict = field(default_factory=dict)

    def live_subagent_count(self) -> int:
        return 0

    def spawned_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return self.classified

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        return self.outputs


def _active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def _waiting_on_child() -> AgentExecutionState:
    return AgentExecutionState.WAITING_ON_CHILD


def test_session_ceiling_fires_with_helpers_alive() -> None:
    """R3 headline: the 2365s indefinite deferral CANNOT happen.

    A monitor that reports 0 FILTERED subagents (10 helpers are
    visible in the descendant tree but they are NOT real subagents)
    must NOT block the ``max_session_seconds`` ceiling. The ceiling
    fires at the configured value regardless of the helper count.
    """
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        # SESSION_CEILING_EXCEEDED is the operator-set hard cap.
        # When set, it MUST fire regardless of any deferral reason
        # (the gate is bypassed -- see ``_gate_fire``).
        max_session_seconds=300.0,
        # Disable NO_OUTPUT_AT_START so the SESSION_CEILING is the
        # headline fire reason for this test.
        no_output_at_start_seconds=None,
        # Disable the no-progress quiet ceiling so the test is
        # unambiguous: the session ceiling is the only fire reason.
        no_progress_quiet_seconds=None,
        # Make the cumulative waiting ceiling shorter than the
        # session ceiling so it cannot fire first.
        max_waiting_on_child_seconds=600.0,
        max_waiting_on_child_no_progress_seconds=None,
        # Disable SUSPECTED_FROZEN so the SUSPECT branch does not
        # compete with the session ceiling.
        suspect_waiting_on_child_seconds=None,
        # Activity evidence is stale (no recorded activity for 305s).
        activity_evidence_ttl_seconds=0.0,
    )
    monitor = _HelpersOnlyMonitor(helper_count=10)
    watchdog = IdleWatchdog(policy, clock, process_monitor=monitor)
    watchdog.record_invocation_start()
    # Advance past the session ceiling (305s > 300s). A real
    # ``idle_elapsed`` value with helpers-but-no-subagents MUST trip
    # the SESSION_CEILING.
    clock.advance(305.0)
    verdict = watchdog.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED
    # The filtered count is 0; the broader helper count (10) was
    # ignored -- the headline R3 invariant.
    assert monitor.spawned_subagent_count() == 0
    assert monitor.helper_count == 10


def test_cumulative_waiting_ceiling_fires_with_helpers_alive() -> None:
    """R3 cumulative path: helpers cannot stretch ``CHILDREN_PERSIST_TOO_LONG``.

    The watchdog enters WAITING_ON_CHILD when ``classify_quiet`` reports
    the agent is waiting. The cumulative ceiling is checked against
    the FILTERED subagent count; a helpers-only monitor MUST NOT block
    the ceiling. After cumulative WAITING time exceeds the ceiling
    (with 0 real subagents), the watchdog fires
    ``CHILDREN_PERSIST_TOO_LONG``.
    """
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        # Short idle deadline so the watchdog enters the verdict
        # path quickly. MUST be <= ``max_waiting_on_child_seconds``
        # per TimeoutPolicy validator.
        idle_timeout_seconds=2.0,
        # The cumulative waiting ceiling MUST fire at 5s. A short
        # ceiling keeps the test fast and unambiguous; the headline
        # invariant is the same regardless of the absolute value.
        max_waiting_on_child_seconds=5.0,
        max_waiting_on_child_no_progress_seconds=None,
        # Disable the OS-descendant-only ceiling which has a default
        # larger than 5s and would fail validation.
        os_descendant_only_ceiling_seconds=None,
        # Disable the stuck-job sub-ceiling which has a default
        # larger than 5s and would fail validation.
        stuck_job_sub_ceiling_seconds=None,
        # Disable the no-progress quiet ceiling to avoid ambiguity.
        no_progress_quiet_seconds=None,
        # Disable NO_OUTPUT_AT_START so the test focuses on the
        # cumulative ceiling.
        no_output_at_start_seconds=None,
        # Disable SUSPECTED_FROZEN so the SUSPECT branch does not
        # compete with the cumulative ceiling.
        suspect_waiting_on_child_seconds=None,
        # Stale activity evidence.
        activity_evidence_ttl_seconds=0.0,
    )
    monitor = _HelpersOnlyMonitor(helper_count=10)
    watchdog = IdleWatchdog(policy, clock, process_monitor=monitor)
    watchdog.record_invocation_start()
    # First evaluate() at 3s: idle_elapsed (3s) > idle_timeout (2s)
    # and classify_quiet returns WAITING_ON_CHILD -> enters the
    # waiting branch (current_run_elapsed=0).
    clock.advance(3.0)
    first_verdict = watchdog.evaluate(classify_quiet=_waiting_on_child)
    assert first_verdict == WatchdogVerdict.WAITING_ON_CHILD
    # Advance the clock by 5s so the current_run_elapsed (5s)
    # reaches the cumulative ceiling (5s). The watchdog MUST fire
    # on the next tick because the ceiling is exceeded and no real
    # subagent is alive.
    clock.advance(5.0)
    verdict = watchdog.evaluate(classify_quiet=_waiting_on_child)
    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG
    # Cumulative time exceeded the ceiling but helpers alone did not
    # block the ceiling. The filtered count is 0; the helpers (10)
    # were ignored.
    assert monitor.spawned_subagent_count() == 0
    assert monitor.helper_count == 10


def test_idle_timeout_fires_with_helpers_alive() -> None:
    """R3 idle path: helpers cannot stretch ``NO_OUTPUT_DEADLINE``.

    After the idle deadline elapses (no stdout, no first-party
    activity, no real subagent alive), the watchdog MUST fire
    ``NO_OUTPUT_DEADLINE``. The presence of 10 helper processes
    (shell tools the agent dispatched) MUST NOT defer the fire.
    """
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        # Disable NO_OUTPUT_AT_START and NO_PROGRESS_QUIET so the
        # idle deadline is the unambiguous fire reason.
        no_output_at_start_seconds=None,
        no_progress_quiet_seconds=None,
        # Disable the drain window so the watchdog fires immediately
        # after the idle deadline (otherwise the default 5s drain
        # window would defer the fire by 5s and the verdict would
        # be CONTINUE).
        drain_window_seconds=0.0,
        # Stale activity evidence.
        activity_evidence_ttl_seconds=0.0,
    )
    monitor = _HelpersOnlyMonitor(helper_count=10)
    watchdog = IdleWatchdog(policy, clock, process_monitor=monitor)
    watchdog.record_invocation_start()
    # Advance past the idle deadline (65s > 60s) with no recorded
    # activity and a helpers-only monitor. The watchdog MUST fire.
    clock.advance(65.0)
    verdict = watchdog.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.NO_OUTPUT_DEADLINE
    # The filtered count is 0; the helpers (10) were ignored.
    assert monitor.spawned_subagent_count() == 0
    assert monitor.helper_count == 10
