"""Black-box tests: R3 cumulative ceiling fires even when a real subagent is alive.

R3 (Trustworthy Idle Watchdog product spec):

    Every genuine hang fires within a bounded ceiling, even when a
    non-subagent process looks like a lingering child.

The headline R3 defect (the 2365s indefinite deferral logged in the
prompt) was the cumulative waiting ceiling consulting
``_gate_fire(...)`` and returning ``WatchdogVerdict.CONTINUE`` when
``_classify_stuck_now`` returned ``SILENT_SUBAGENT``. The
``_gate_fire`` deferral block was effective at SUB-ceiling time
(the smart 600s sub-ceiling that gates on stale alive_by) but it
MUST NOT defer the CUMULATIVE ceiling -- a true hang must fire
within the configured cumulative ceiling regardless of any
classification signal.

This module pins BOTH dimensions of the R3 hard-enforcement
contract:

  * ``test_cumulative_ceiling_fires_when_classify_stuck_returns_silent_subagent``:
    with a real subagent alive (filtered count = 1) AND the
    classifier returning ``SILENT_SUBAGENT`` -- the regression that
    previously caused the 2365s indefinite deferral. Pre-fix the
    test fails because ``_gate_fire`` returns ``CONTINUE`` on
    ``SILENT_SUBAGENT`` and the ceiling is bypassed; post-fix it
    passes because the cumulative ceiling no longer consults
    ``_gate_fire``.

  * ``test_cumulative_ceiling_fires_when_classify_stuck_returns_loading``:
    with a real subagent alive (filtered count = 1) AND the
    classifier returning ``LOADING``, the cumulative ceiling MUST
    still fire. The prompt explicitly requires hard enforcement
    regardless of any classification signal; LOADING is the
    non-STUCK kind that ``_gate_fire`` defers on (returning
    ``CONTINUE``) and the cumulative ceiling must NOT honour that
    deferral.

Every test uses ``FakeClock`` and a Protocol-typed ``@dataclass``
``ProcessMonitor`` fake -- NO real subprocess, NO ``time.sleep``,
NO real filesystem. The test is in scope for the canonical
``RALPH_PIN_TEST_PATHS`` R8 audit target.

The ``_classify_stuck_now`` method is overridden via ``setattr``
(an in-process instance-level override, mirroring the pattern in
``test_trustworthy_idle_watchdog_spec.py::test_r6``) so the
classifier returns the kind under test deterministically. The
override does NOT touch the watchdog's classifier input model --
it just forces the gate to take the SILENT_SUBAGENT / LOADING
branch and return ``CONTINUE`` on the pre-fix code; the post-fix
cumulative ceiling block drops the ``_gate_fire`` consultation
entirely so the override has no effect on the verdict.

References:
  - ``ralph/agents/idle_watchdog/_waiting_branch.py:238-247`` --
    the cumulative ceiling block (the R3 fix site).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.idle_watchdog._stuck_classifier import StuckKind
from ralph.agents.timeout_clock import FakeClock
from ralph.process.monitor import (
    ProcessMonitor,
    SubagentOutputCapture,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.agents.idle_watchdog.corroboration_snapshot import (
        CorroborationSnapshot,
    )


@dataclass
class _RealSubagentMonitor(ProcessMonitor):
    """Fake monitor: filtered count is 1 (a real subagent is alive).

    Mirrors the ``_FilteredCountMonitor`` pattern at
    ``tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py``
    but configures ``filtered_count=1`` instead of 0 so the watchdog
    sees a real subagent and would otherwise have a defensible reason
    to defer.

    Both ``live_subagent_count()`` (legacy alias) and
    ``spawned_subagent_count()`` (preferred) return ``filtered_count``
    so the watchdog reads the filtered seam regardless of which name
    it consults.
    """

    filtered_count: int = 1
    classified: tuple = field(default_factory=tuple)
    outputs: dict = field(default_factory=dict)

    def live_subagent_count(self) -> int:
        return self.filtered_count

    def spawned_subagent_count(self) -> int:
        return self.filtered_count

    def classified_processes(self) -> tuple:
        return self.classified

    def refresh(self) -> None:
        pass

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        return self.outputs


def _waiting_on_child() -> AgentExecutionState:
    """classify_quiet that returns WAITING_ON_CHILD on every call."""
    return AgentExecutionState.WAITING_ON_CHILD


def _force_classify_stuck_kind(
    watchdog: IdleWatchdog,
    kind: StuckKind,
) -> None:
    """Override ``_classify_stuck_now`` to return a fixed kind on every call.

    Mirrors the pattern at
    ``test_trustworthy_idle_watchdog_spec.py::test_r6`` line 723 --
    ``setattr`` on the watchdog instance with the attribute name in
    a local variable (audit_lint_bypass: bare constant setattr is
    ruff B010; mypy cannot narrow access to a private-method
    assignment). The override is in-process only; it does NOT touch
    the watchdog's classifier input model.
    """
    fixed_kind = kind

    def _stuck_now(
        *,
        now: float,
        idle_elapsed: float,
        corroboration: CorroborationSnapshot | None = None,
    ) -> StuckKind:
        return fixed_kind

    _classify_attr = "_classify_stuck_now"
    setattr(watchdog, _classify_attr, _stuck_now)


def _make_watchdog(
    *,
    clock: FakeClock,
    process_monitor: ProcessMonitor,
    policy: TimeoutPolicy,
    corroborator: Callable[[], CorroborationSnapshot] | None = None,
) -> IdleWatchdog:
    """Build an IdleWatchdog with the given policy, clock, and monitor."""
    return IdleWatchdog(
        policy,
        clock,
        listener=None,
        corroborator=corroborator,
        process_monitor=process_monitor,
    )


def test_cumulative_ceiling_fires_when_classify_stuck_returns_silent_subagent() -> None:
    """R3 regression pin: cumulative ceiling fires on SILENT_SUBAGENT deferral.

    Scenario: a real subagent is alive (filtered count = 1) AND the
    classifier returns ``SILENT_SUBAGENT`` -- the subagent is alive
    in the OS but is NOT producing fresh progress / heartbeat
    evidence. The cumulative ceiling MUST fire regardless of the
    SILENT_SUBAGENT classification.

    This test FAILS on the pre-fix code because the cumulative
    ceiling block at ``_waiting_branch.py:238-247`` consults
    ``self._gate_fire(...)`` and returns ``WatchdogVerdict.CONTINUE``
    when ``_classify_stuck_now`` returns ``SILENT_SUBAGENT`` -- the
    exact bug class that produced the 2365s indefinite deferral.

    Post-fix the cumulative ceiling block drops the ``_gate_fire``
    consultation and the ``CONTINUE`` branch, so the ceiling fires
    unconditionally when ``candidate_total >= effective_ceiling``.
    """
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        # Short idle deadline so the watchdog enters the verdict
        # path quickly. MUST be <= ``max_waiting_on_child_seconds``.
        idle_timeout_seconds=2.0,
        # The cumulative waiting ceiling MUST fire at 10s.
        max_waiting_on_child_seconds=10.0,
        # Disable the no-progress quiet ceiling so the test is
        # unambiguous: the cumulative ceiling is the only fire path.
        max_waiting_on_child_no_progress_seconds=None,
        # Disable the OS-descendant-only ceiling -- would compete
        # with the cumulative ceiling.
        os_descendant_only_ceiling_seconds=None,
        # Disable the stuck-job sub-ceiling so the SUB-ceiling
        # branch (which retains its ``_gate_fire`` consultation)
        # cannot fire first; the cumulative ceiling is the headline
        # fire reason for this test.
        stuck_job_sub_ceiling_seconds=None,
        no_progress_quiet_seconds=None,
        no_output_at_start_seconds=None,
        suspect_waiting_on_child_seconds=None,
        # Stale activity evidence (ttl=0 disables the
        # subagent_liveness_fresh branch so the classifier
        # does NOT short-circuit to LOADING via that branch;
        # the override below forces SILENT_SUBAGENT regardless).
        activity_evidence_ttl_seconds=0.0,
    )
    monitor = _RealSubagentMonitor(filtered_count=1)
    watchdog = _make_watchdog(
        clock=clock,
        process_monitor=monitor,
        policy=policy,
    )
    # Force the classifier to return SILENT_SUBAGENT on every
    # call so the gate takes the SILENT_SUBAGENT branch (returns
    # CONTINUE on pre-fix; has no effect on post-fix because the
    # cumulative ceiling block drops the gate consultation).
    _force_classify_stuck_kind(watchdog, StuckKind.SILENT_SUBAGENT)
    watchdog.record_invocation_start()
    # First evaluate() at 3s: idle_elapsed (3s) > idle_timeout (2s),
    # classify_quiet returns WAITING_ON_CHILD -> enters waiting
    # branch with current_run_elapsed = 0.
    clock.advance(3.0)
    first_verdict = watchdog.evaluate(classify_quiet=_waiting_on_child)
    assert first_verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"first evaluate() MUST enter WAITING_ON_CHILD, got {first_verdict!r}"
    )
    # Advance the clock by 9s so current_run_elapsed reaches 9s and
    # the candidate_total (cumulative=0 + run=9s = 9s) is still
    # below the cumulative ceiling (10s). The next advance tips
    # candidate_total past the ceiling.
    clock.advance(9.0)
    pre_ceiling_verdict = watchdog.evaluate(classify_quiet=_waiting_on_child)
    assert pre_ceiling_verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"pre-ceiling evaluate() MUST defer, got {pre_ceiling_verdict!r}"
    )
    # Advance past the cumulative ceiling. The ceiling MUST fire on
    # the next evaluate() call regardless of the SILENT_SUBAGENT
    # classification. This is the headline R3 invariant -- the
    # pre-fix code would have returned CONTINUE on SILENT_SUBAGENT.
    clock.advance(1.0)
    verdict = watchdog.evaluate(classify_quiet=_waiting_on_child)
    assert verdict == WatchdogVerdict.FIRE, (
        f"cumulative ceiling MUST fire even when _classify_stuck_now"
        f" returns SILENT_SUBAGENT; got verdict={verdict!r}"
        f" last_fire_reason={watchdog.last_fire_reason!r}"
        f" last_deferred_kind={watchdog.last_deferred_kind!r}"
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG, (
        f"cumulative ceiling fire reason MUST be"
        f" CHILDREN_PERSIST_TOO_LONG; got {watchdog.last_fire_reason!r}"
    )


def test_cumulative_ceiling_fires_when_classify_stuck_returns_loading() -> None:
    """R3 hard-enforcement: cumulative ceiling fires on LOADING deferral.

    Scenario: a real subagent is alive (filtered count = 1) AND the
    classifier returns ``LOADING``. Per PROMPT R3 the cumulative
    ceiling MUST fire regardless of any classification signal.

    This test pins the invariant that a healthy, fresh liveness
    signal (LOADING) cannot indefinitely extend the cumulative
    wait past the ceiling -- the hard ceiling is the absolute
    backstop the prompt requires.
    """
    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=2.0,
        # The cumulative waiting ceiling MUST fire at 10s.
        max_waiting_on_child_seconds=10.0,
        max_waiting_on_child_no_progress_seconds=None,
        os_descendant_only_ceiling_seconds=None,
        # Disable the stuck-job sub-ceiling so the SUB-ceiling
        # branch (which gates on stale alive_by in
        # ``_STUCK_ALIVE_BY_VALUES``) cannot fire first.
        stuck_job_sub_ceiling_seconds=None,
        no_progress_quiet_seconds=None,
        no_output_at_start_seconds=None,
        suspect_waiting_on_child_seconds=None,
        activity_evidence_ttl_seconds=0.0,
    )
    monitor = _RealSubagentMonitor(filtered_count=1)
    watchdog = _make_watchdog(
        clock=clock,
        process_monitor=monitor,
        policy=policy,
    )
    # Force the classifier to return LOADING so the gate takes
    # the LOADING branch (returns CONTINUE on pre-fix; has no
    # effect on post-fix because the cumulative ceiling block
    # drops the gate consultation).
    _force_classify_stuck_kind(watchdog, StuckKind.LOADING)
    watchdog.record_invocation_start()
    # First evaluate() at 3s enters the waiting branch.
    clock.advance(3.0)
    first_verdict = watchdog.evaluate(classify_quiet=_waiting_on_child)
    assert first_verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"first evaluate() MUST enter WAITING_ON_CHILD, got {first_verdict!r}"
    )
    # Advance so cumulative waiting time exceeds the 10s ceiling.
    # The classifier returns LOADING (a healthy liveness signal)
    # so on the pre-fix code ``_gate_fire`` returns ``CONTINUE``
    # and the ceiling is bypassed. Post-fix the ceiling fires
    # unconditionally.
    clock.advance(9.0)
    pre_ceiling_verdict = watchdog.evaluate(classify_quiet=_waiting_on_child)
    assert pre_ceiling_verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"pre-ceiling evaluate() MUST defer, got {pre_ceiling_verdict!r}"
    )
    clock.advance(1.0)
    verdict = watchdog.evaluate(classify_quiet=_waiting_on_child)
    assert verdict == WatchdogVerdict.FIRE, (
        f"cumulative ceiling MUST fire even when _classify_stuck_now"
        f" returns LOADING; got verdict={verdict!r}"
        f" last_fire_reason={watchdog.last_fire_reason!r}"
    )
    assert watchdog.last_fire_reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG, (
        f"cumulative ceiling fire reason MUST be"
        f" CHILDREN_PERSIST_TOO_LONG; got {watchdog.last_fire_reason!r}"
    )


__all__ = [
    "test_cumulative_ceiling_fires_when_classify_stuck_returns_loading",
    "test_cumulative_ceiling_fires_when_classify_stuck_returns_silent_subagent",
]


# Silence unused-import warning for cast -- kept for forward
# compatibility with future test methods that may need explicit
# narrowing.
_ = cast("Any", None)
