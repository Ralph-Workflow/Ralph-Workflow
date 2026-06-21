"""Resume-contract invariant for every WatchdogFireReason member.

The watchdog has a canonical resumable-fire-reason set
(``_RESUMABLE_FIRE_REASONS`` in ``ralph/agents/invoke/_process_reader.py``)
and a documented exclusion set of non-resumable reasons. This test pins the
exhaustive partition: every enum member must be in one set or the other, and
the diagnostic-only reason ``DEFERRED_BY_STUCK_CLASSIFIER`` must never be
returned as a FIRE verdict.

All tests use FakeClock; no real subprocess, no time.sleep, no real network.
"""

from __future__ import annotations

from dataclasses import dataclass

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.invoke._process_reader import _RESUMABLE_FIRE_REASONS
from ralph.agents.timeout_clock import FakeClock


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


def _active() -> AgentExecutionState:
    return AgentExecutionState.ACTIVE


def _make_watchdog() -> tuple[IdleWatchdog, FakeClock]:
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    return IdleWatchdog(policy, clock, process_monitor=_NoProcessMonitor()), clock


_NON_RESUMABLE_REASONS: frozenset[WatchdogFireReason] = frozenset(
    {
        WatchdogFireReason.PROCESS_EXIT_HANG,
        WatchdogFireReason.DESCENDANT_HANG,
        WatchdogFireReason.SESSION_CEILING_EXCEEDED,
        WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
        WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER,
        WatchdogFireReason.STRICTLY_STUCK,
    }
)


def test_every_fire_reason_is_classified() -> None:
    """Every ``WatchdogFireReason`` member is either resumable or explicitly
    non-resumable.

    A future PR that adds a new reason without updating either the canonical
    resumable set or the explicit exclusion set fails this assertion,
    preventing silent drift of the resume contract.
    """
    for reason in WatchdogFireReason.__members__.values():
        assert reason in _RESUMABLE_FIRE_REASONS or reason in _NON_RESUMABLE_REASONS, (
            f"reason={reason!r} is neither in _RESUMABLE_FIRE_REASONS nor in the"
            f" explicit non-resumable exclusion set; update the resume contract"
        )


def test_resumable_and_non_resumable_sets_are_disjoint() -> None:
    """No reason may be both resumable and non-resumable."""
    overlap = _RESUMABLE_FIRE_REASONS & _NON_RESUMABLE_REASONS
    assert not overlap, f"resumable and non-resumable sets overlap: {overlap!r}"


def test_deferred_by_stuck_classifier_never_fires() -> None:
    """``DEFERRED_BY_STUCK_CLASSIFIER`` is only a diagnostic label.

    We force a candidate ``NO_OUTPUT_AT_START`` fire while the pipeline is in
    a wait state. The smart-verdict gate defers the fire and sets
    ``last_fire_reason`` to ``DEFERRED_BY_STUCK_CLASSIFIER``, but the returned
    verdict is ``CONTINUE``, never ``FIRE``.

    The dumb-kill floor (``no_progress_quiet_minimum_invocation_seconds``)
    defaults to 120 s and now defers the fire before the gate, so the test
    disables the floor to reach the gate path. The test still proves the
    gate defers when the classifier returns ``DUPLICATE_KILL`` for a wait
    state.
    """
    clock = FakeClock(start=0.0)
    from ralph.agents.idle_watchdog import TimeoutPolicy as _TP
    policy = _TP(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=30.0,
        no_progress_quiet_seconds=None,
        no_progress_quiet_minimum_invocation_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    watchdog = IdleWatchdog(policy, clock, process_monitor=_NoProcessMonitor())
    watchdog.record_invocation_start()
    watchdog.set_is_waiting_state(True)

    clock.advance(31.0)
    verdict = watchdog.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.CONTINUE, (
        f"expected CONTINUE for deferred DEFERRED_BY_STUCK_CLASSIFIER; got {verdict}"
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER, (
        f"expected last_fire_reason={WatchdogFireReason.DEFERRED_BY_STUCK_CLASSIFIER!r};"
        f" got {watchdog.last_fire_reason!r}"
    )
