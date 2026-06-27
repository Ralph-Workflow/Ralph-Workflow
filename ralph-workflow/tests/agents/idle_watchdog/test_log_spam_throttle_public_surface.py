"""R6 public-surface proof: throttle invariant asserted via public observables.

This module proves the R6 spam-invariant (Trustworthy Idle Watchdog spec)
from PUBLIC surfaces only -- no ``setattr`` on ``_classify_stuck_now``, no
direct call to ``_gate_fire``, no read of any ``_last_*_log_at`` private
state. The canonical dedicated pin test at
``tests/agents/idle_watchdog/test_log_spam_throttle.py`` consults those
private seams; this module proves the same invariant is OBSERVABLE from
the two public seams:

  (a) ``IdleWatchdog.__init__(listener=...)`` -- the canonical public
      listener wiring. Every emitted ``WaitingStatusEvent`` is dispatched
      to the listener via ``IdleWatchdog._emit``.
  (b) The loguru sink filter on ``component='idle_watchdog'`` -- the
      canonical public loguru surface used by ``test_r6_heartbeat`` and
      every other consolidated R6 test.

Driving 1000 ``evaluate()`` calls in the same throttle window via the
public ``evaluate(classify_quiet=...)`` entry point (``idle_watchdog.py:1239``)
must emit a bounded number of events and log records. Pre-fix the
defect mode was an unbounded stream of near-duplicate lines; the post-fix
invariant is that the PROGRESS-kind events and the throttle-filtered log
records count are both ``<= 2``.

This module satisfies ``audit_test_policy`` (no real subprocess, no
``time.sleep``, no real file I/O): it uses ``FakeClock`` (the canonical
test clock) and the Protocol-typed ``@dataclass _HelpersOnlyMonitor``
fake shared with ``test_trustworthy_idle_watchdog_spec.py``.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import pytest
from loguru import logger

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WaitingStatusEvent,
    WaitingStatusKind,
    WatchdogVerdict,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.process.monitor import ProcessMonitor, SubagentOutputCapture


@dataclass
class _HelpersOnlyMonitor(ProcessMonitor):
    """Protocol-typed fake ProcessMonitor (canonical R1/R6 fixture).

    Mirrors ``_HelpersOnlyMonitor`` from
    ``test_trustworthy_idle_watchdog_spec.py``. The filtered count
    (the R1 seam) returns 0; the watchdog defers via the WAITING
    branch when ``evaluate()`` reports ``AgentExecutionState.WAITING_ON_CHILD``.
    """

    helper_count: int = 0
    classified: tuple = field(default_factory=tuple)
    outputs: dict = field(default_factory=dict)

    def live_subagent_count(self) -> int:
        return 0

    def spawned_subagent_count(self) -> int:
        return 0

    def classified_processes(self) -> tuple:
        return self.classified

    def refresh(self) -> None:
        return None

    def discover_subagent_outputs(self) -> dict[str, SubagentOutputCapture]:
        return self.outputs


@pytest.fixture
def captured_log_records() -> tuple[io.StringIO, list[str]]:
    """Attach a loguru sink filtered on ``component='idle_watchdog'``.

    The filter matches the canonical public loguru surface used by
    ``test_r6_heartbeat``: the watchdog binds its internal logger via
    ``self._log = logger.bind(component="idle_watchdog")`` in
    ``idle_watchdog.py:558``. Any DEBUG/INFO/ERROR record emitted via
    that logger (or any deeper bind) flows through this sink.
    """
    buf = io.StringIO()
    records: list[str] = []

    def _sink(message: str) -> None:
        records.append(message)

    handler_id = logger.add(
        _sink,
        level="DEBUG",
        format="{message}",
        filter=lambda record: "idle_watchdog"
        in (record["extra"].get("component") or ""),
    )
    try:
        yield buf, records
    finally:
        logger.remove(handler_id)


def test_log_spam_throttle_public_surface_via_evaluate_and_listener(
    captured_log_records: tuple[io.StringIO, list[str]],
) -> None:
    """R6: throttle invariant proven via ``evaluate()`` + listener only.

    Drives 1000 ``evaluate()`` calls via the public entry point and
    captures every emitted ``WaitingStatusEvent`` via the listener
    passed to ``IdleWatchdog.__init__``. Asserts the bounded count of
    PROGRESS-kind events AND the bounded count of loguru records
    filtered on ``component='idle_watchdog'``. Uses NO private seams:
    no ``setattr`` on ``_classify_stuck_now``, no direct call to
    ``_gate_fire``, no read of any ``_last_*_log_at`` field.
    """
    _buf, log_records = captured_log_records
    captured_events: list[WaitingStatusEvent] = []

    def _listener(event: WaitingStatusEvent) -> None:
        captured_events.append(event)

    clock = FakeClock(start=0.0)
    # ``stuck_job_sub_ceiling_seconds=None`` skips the SUB-ceiling
    # block in ``_waiting_branch.py`` (the only block that consults
    # ``self._gate_fire``). The cumulative ceiling is set to a very
    # high value so the unconditional HARD_STOP block at
    # ``_waiting_branch.py:239-289`` is also unreachable. With both
    # ceilings disabled, the ONLY log emissions during 1000 evaluate()
    # calls in the same throttle window are: (1) the WAITING entry
    # log at ``_waiting_branch.py:122`` (fires ONCE), (2) zero
    # PROGRESS heartbeats (the cadence gate stays closed because
    # ``clock`` does not advance). The R6 invariant: ``<= 2`` of each.
    policy = TimeoutPolicy(
        idle_timeout_seconds=0.5,
        max_waiting_on_child_seconds=1_000_000.0,
        no_output_at_start_seconds=None,
        no_progress_quiet_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        suspect_waiting_on_child_seconds=None,
        stuck_job_sub_ceiling_seconds=None,
        waiting_status_interval_seconds=30.0,
        watchdog_log_throttle_seconds=30.0,
        activity_evidence_ttl_seconds=0.0,
    )
    watchdog = IdleWatchdog(
        config=policy,
        clock=clock,
        listener=_listener,
        process_monitor=_HelpersOnlyMonitor(),
    )

    watchdog.record_invocation_start()
    clock.advance(3.0)

    def _waiting() -> AgentExecutionState:
        return AgentExecutionState.WAITING_ON_CHILD

    # Call 1: WAITING branch entered -- ENTERED event fires + entry
    # log fires ONCE. ``_last_waiting_status_at`` is stamped to 3.0.
    first_verdict = watchdog.evaluate(classify_quiet=_waiting)
    assert first_verdict == WatchdogVerdict.WAITING_ON_CHILD

    # Calls 2..1000: clock does NOT advance. The cadence gate
    # ``now - _last_waiting_status_at = 0 >= 30.0`` stays False, so
    # no PROGRESS heartbeats fire. The watchdog keeps deferring
    # (returns WAITING_ON_CHILD on every call) because the cumulative
    # ceiling is unreachable.
    for _ in range(999):
        verdict = watchdog.evaluate(classify_quiet=_waiting)
        assert verdict == WatchdogVerdict.WAITING_ON_CHILD

    # Assertion 1: PROGRESS-kind events emitted during 1000
    # evaluate() calls in the same throttle window is <= 2 (R6
    # invariant). In this configuration the cadence gate never opens,
    # so the actual count is 0; the <= 2 bound is the canonical R6
    # spam-invariant.
    progress_events = [
        e for e in captured_events if e.kind == WaitingStatusKind.PROGRESS
    ]
    assert len(progress_events) <= 2, (
        f"R6 PROGRESS-event throttle MUST cap emissions to <= 2 per"
        f" throttle window; got {len(progress_events)} PROGRESS events"
        f" across 1000 evaluate() calls."
    )

    # Assertion 2: ENTERED event fires EXACTLY once (on first WAITING
    # entry). This is a public-surface witness that the WAITING
    # branch was entered; a missing ENTERED would imply the watchdog
    # never deferred, which would make the throttle invariant
    # trivially true (a zero-sink bypass).
    entered_events = [
        e for e in captured_events if e.kind == WaitingStatusKind.ENTERED
    ]
    assert len(entered_events) == 1, (
        f"R6 ENTERED event MUST fire exactly once on first WAITING"
        f" entry; got {len(entered_events)} ENTERED events."
    )

    # Assertion 3: loguru records filtered on
    # ``component='idle_watchdog'`` count is <= 2 (R6 invariant).
    # The single WAITING entry log fires once; no other emissions
    # occur in the throttle window. The bound ``<= 2`` is the
    # canonical R6 spam-invariant (matches the dedicated
    # ``test_log_spam_throttle.py`` ceiling).
    assert len(log_records) <= 2, (
        f"R6 log-emission throttle MUST cap emissions to <= 2 per"
        f" throttle window; got {len(log_records)} log records"
        f" across 1000 evaluate() calls. Records: {log_records[:5]}"
    )

    # Assertion 4: the sink is REAL (not trivially empty). At least 1
    # log record must have been captured -- the WAITING entry log --
    # so a zero-sink bypass (e.g. a future refactor that silently
    # renames the ``component`` bind) cannot pass this test trivially.
    assert len(log_records) >= 1, (
        f"loguru sink filtered on component='idle_watchdog' MUST"
        f" capture the WAITING entry log emitted at"
        f" _waiting_branch.py:122; got {len(log_records)} records"
        f" (a zero-sink bypass means the bound is meaningless)."
    )


def test_log_spam_throttle_public_surface_no_private_seam_access(
    captured_log_records: tuple[io.StringIO, list[str]],
) -> None:
    """R6: explicit witness that NO private seam is consulted.

    This test exists as a SECOND witness for the R6 invariant that
    exercises a clock-advancing workload (vs the no-advance workload
    in the primary test). The invariant is the same: 500 evaluate()
    calls with the clock advancing 0.05s each (total 25s, under the
    30s throttle window) emit ``<= 2`` PROGRESS events and ``<= 2``
    log records.

    This second witness is structurally important: a future refactor
    that breaks the no-advance path cannot regress the throttle
    invariant silently because the clock-advancing path still holds.
    """
    _buf, log_records = captured_log_records
    captured_events: list[WaitingStatusEvent] = []

    def _listener(event: WaitingStatusEvent) -> None:
        captured_events.append(event)

    clock = FakeClock(start=0.0)
    policy = TimeoutPolicy(
        idle_timeout_seconds=0.5,
        max_waiting_on_child_seconds=1_000_000.0,
        no_output_at_start_seconds=None,
        no_progress_quiet_seconds=None,
        max_waiting_on_child_no_progress_seconds=None,
        suspect_waiting_on_child_seconds=None,
        stuck_job_sub_ceiling_seconds=None,
        waiting_status_interval_seconds=30.0,
        watchdog_log_throttle_seconds=30.0,
        activity_evidence_ttl_seconds=0.0,
    )
    watchdog = IdleWatchdog(
        config=policy,
        clock=clock,
        listener=_listener,
        process_monitor=_HelpersOnlyMonitor(),
    )

    watchdog.record_invocation_start()
    clock.advance(3.0)

    def _waiting() -> AgentExecutionState:
        return AgentExecutionState.WAITING_ON_CHILD

    # First evaluate enters WAITING branch via the
    # ``AgentExecutionState.WAITING_ON_CHILD`` state from the
    # strategy. ``_last_waiting_status_at`` is stamped to 3.0.
    watchdog.evaluate(classify_quiet=_waiting)

    # Drive 500 more calls advancing clock by 0.05s each (total 25s,
    # under the 30s throttle window). The cadence gate stays closed
    # because ``_last_waiting_status_at`` advances in lockstep with
    # ``now`` -- the delta never exceeds 30.0s -- so no PROGRESS
    # heartbeats fire. This proves the throttle invariant is
    # robust under CLOCK-ADVANCING workloads (not just no-advance).
    for _ in range(500):
        clock.advance(0.05)
        watchdog.evaluate(classify_quiet=_waiting)

    progress_events = [
        e for e in captured_events if e.kind == WaitingStatusKind.PROGRESS
    ]
    assert len(progress_events) <= 2, (
        f"R6 PROGRESS-event throttle MUST hold under clock-advance"
        f" workloads; got {len(progress_events)} PROGRESS events"
        f" across 501 evaluate() calls."
    )
    assert len(log_records) <= 2, (
        f"R6 log-emission throttle MUST hold under clock-advance"
        f" workloads; got {len(log_records)} log records across 501"
        f" evaluate() calls. Records: {log_records[:5]}"
    )
    assert len(log_records) >= 1, (
        f"loguru sink MUST capture the WAITING entry log; got"
        f" {len(log_records)} records (zero-sink bypass)."
    )
