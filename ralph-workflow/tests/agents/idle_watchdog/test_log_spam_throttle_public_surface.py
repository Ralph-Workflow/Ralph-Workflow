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

The headline R6 regression was ~10 DEBUG records/sec emitted at
``_gate_fire:949`` while a fire was deferred with ``SILENT_SUBAGENT``
(the ``CHILDREN_PERSIST_TOO_LONG`` deferred-fire path produced by the
SUB-ceiling block at ``_waiting_branch.py:184-237``). The fix added a
per-(fire_reason, deferred_kind) throttle plus a coarse single-key
throttle so the throttle holds even when the deferred_kind cycles
between calls.

Three tests prove the R6 invariant from public observables:

  1. ``test_log_spam_throttle_public_surface_reaches_deferred_fire_branch``:
     identical-SILENT_SUBAGENT scenario -- 1000 evaluate() calls in
     the same throttle window with the same SILENT_SUBAGENT
     deferred_kind return <= 2 DEBUG records.
  2. ``test_log_spam_throttle_public_surface_deferred_fire_throttle_window``:
     secondary throttle-window refresh witness with a 0.05s
     ``watchdog_log_throttle_seconds`` (proves the per-tuple
     refresh boundary holds in public surface too).
  3. ``test_log_spam_throttle_public_surface_kind_cycle_via_public_surface``:
     kind-cycle scenario -- 1000 evaluate() calls alternating the
     classifier's ``is_waiting_state`` input between False
     (SILENT_SUBAGENT) and True (DUPLICATE_KILL) via the PUBLIC
     ``watchdog.set_is_waiting_state(bool)`` method, proving the
     COARSE single-key throttle caps emissions across the
     deferred_kind cycle that the per-tuple throttle MISSED pre-fix.

Test 3 mirrors the private-seam
``tests/agents/idle_watchdog/test_log_spam_throttle.py::test_coarse_single_key_throttle_caps_emissions_across_kind_cycles``
exactly -- the public-surface proof for the same deferred_kind cycle
(SILENT_SUBAGENT <-> DUPLICATE_KILL, with the cycle driven by the
run-loop-facing ``set_is_waiting_state`` method instead of a
``setattr`` on ``_classify_stuck_now``).

The module is black-box: ``FakeClock`` + Protocol-typed
``@dataclass _HelpersOnlyMonitor`` fake; no real subprocess, no real
sleep, no real file I/O.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

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
from ralph.agents.idle_watchdog._stuck_classifier import StuckKind
from ralph.agents.idle_watchdog.corroboration_snapshot import (
    CorroborationSnapshot,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.process.child_liveness import AliveBy
from ralph.process.monitor import ProcessMonitor, SubagentOutputCapture

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "test_log_spam_throttle_public_surface_deferred_fire_throttle_window",
    "test_log_spam_throttle_public_surface_kind_cycle_via_public_surface",
    "test_log_spam_throttle_public_surface_reaches_deferred_fire_branch",
]


@dataclass
class _HelpersOnlyMonitor(ProcessMonitor):
    """Protocol-typed fake ProcessMonitor (canonical R1/R6 fixture).

    Mirrors ``_HelpersOnlyMonitor`` from
    ``test_trustworthy_idle_watchdog_spec.py``. The filtered count
    (the R1 seam) returns 0; ``live_subagent_count()`` also returns
    0 so the watchdog's subagent_liveness channel has
    ``alive_by=None`` and ``can_defer=False``. With no live
    subagent signal the StuckClassifier falls through to the
    SILENT_SUBAGENT branch when ``subagent_output`` channel has
    stale evidence (the regression scenario).
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


def _stale_subagent_corroborator() -> CorroborationSnapshot:
    """Corroborator that reports a stuck-but-alive child.

    Returns ``scoped_child_active=True`` and
    ``alive_by=OS_DESCENDANT_ONLY_STALE_PROGRESS`` so the
    SUB-ceiling block at ``_waiting_branch.py:184-237`` is reached
    (the block requires BOTH conditions plus
    ``candidate_total >= stuck_job_sub_ceiling_seconds``).
    """
    return CorroborationSnapshot(
        scoped_child_active=True,
        alive_by=AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS,
    )


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
        filter=lambda record: "idle_watchdog" in (record["extra"].get("component") or ""),
    )
    try:
        yield buf, records
    finally:
        logger.remove(handler_id)


def _build_deferred_fire_watchdog(
    *,
    listener: Callable[[WaitingStatusEvent], None] | None,
    clock: FakeClock,
    silent_subagent_seconds: float = 1.0,
    stuck_job_sub_ceiling_seconds: float = 5.0,
    max_waiting_on_child_seconds: float = 10_000.0,
    watchdog_log_throttle_seconds: float = 30.0,
) -> IdleWatchdog:
    """Construct an IdleWatchdog wired to exercise the deferred-fire path.

    The configuration is intentionally minimal so the deferred-fire
    branch at ``_waiting_branch.py:184-237`` is reachable via public
    behavior:

      * ``stuck_job_sub_ceiling_seconds=5.0`` enables the SUB-ceiling
        block (the only block that consults ``_gate_fire``).
      * ``max_waiting_on_child_seconds=10_000.0`` makes the cumulative
        ceiling unreachable, so the watchdog cannot bypass the
        SUB-ceiling block via the cumulative hard stop.
      * ``silent_subagent_seconds=1.0`` enables the SILENT_SUBAGENT
        branch of the StuckClassifier. With a stale ``subagent_output``
        channel (seeded via ``record_subagent_work``) and
        ``subagent_liveness`` showing ``alive_by=None`` (because
        ``_HelpersOnlyMonitor.live_subagent_count() == 0``), the
        classifier returns ``SILENT_SUBAGENT`` and ``_gate_fire``
        returns ``CONTINUE`` -- the deferred-fire path.
      * ``activity_evidence_ttl_seconds=0.0`` disables the
        first-party / side-channel freshness deferrals so the
        classifier does NOT short-circuit to ``THINKING`` or
        ``LOADING`` via those branches; the only remaining
        non-STUCK branch is SILENT_SUBAGENT.
      * ``waiting_status_interval_seconds=10_000.0`` (and
        ``watchdog_subagent_progress_interval_seconds=10_000.0``)
        keep the cadence gates closed so PROGRESS /
        SUBAGENT_PROGRESS events are NOT emitted during the 1000-call
        cycle (the spam-relevant emissions come from the
        deferred-fire branch, not the cadence gates).
    """
    policy = TimeoutPolicy(
        idle_timeout_seconds=2.0,
        # Short idle deadline so ``evaluate()`` reaches the
        # WAITING branch quickly; not directly used because
        # WAITING_ON_CHILD branch is consulted before the idle
        # deadline per ``evaluate()`` priority order.
        max_waiting_on_child_seconds=max_waiting_on_child_seconds,
        # Cumulative ceiling far above the SUB-ceiling so the
        # cumulative hard stop cannot fire and bypass the
        # SUB-ceiling deferred-fire branch.
        max_waiting_on_child_no_progress_seconds=None,
        # Disable orthogonal no-progress / strictly-stuck ceilings so
        # the SUB-ceiling block is the only fire path consulted.
        os_descendant_only_ceiling_seconds=None,
        os_descendant_only_suspect_seconds=None,
        no_progress_quiet_seconds=None,
        no_progress_quiet_strictly_stuck_seconds=None,
        no_progress_quiet_heartbeat_ceiling_seconds=None,
        no_output_at_start_seconds=None,
        suspect_waiting_on_child_seconds=None,
        # SUB-ceiling: the headline fire reason consulted by the
        # deferred-fire spam regression.
        stuck_job_sub_ceiling_seconds=stuck_job_sub_ceiling_seconds,
        # Disable freshness deferrals so the classifier cannot
        # short-circuit to THINKING/LOADING and the SILENT_SUBAGENT
        # branch is the only non-STUCK branch reachable.
        activity_evidence_ttl_seconds=0.0,
        # SILENT_SUBAGENT branch threshold.
        silent_subagent_seconds=silent_subagent_seconds,
        # Cadence gates closed during the 1000-call cycle so the
        # throttle proof isolates the deferred-fire emissions.
        waiting_status_interval_seconds=10_000.0,
        watchdog_log_throttle_seconds=watchdog_log_throttle_seconds,
        watchdog_subagent_progress_interval_seconds=10_000.0,
    )
    return IdleWatchdog(
        config=policy,
        clock=clock,
        listener=listener,
        corroborator=_stale_subagent_corroborator,
        process_monitor=_HelpersOnlyMonitor(),
    )


def test_log_spam_throttle_public_surface_reaches_deferred_fire_branch(
    captured_log_records: tuple[io.StringIO, list[str]],
) -> None:
    """R6: deferred-fire branch reached via PUBLIC surface only.

    The headline R6 regression was ~10 DEBUG records/sec emitted at
    ``_gate_fire:949`` while a fire was deferred with
    ``SILENT_SUBAGENT`` via the ``CHILDREN_PERSIST_TOO_LONG`` deferred
    path produced by the SUB-ceiling block at
    ``_waiting_branch.py:184-237``. The throttle fix caps emissions
    to <= 1 per ``(fire_reason, deferred_kind)`` key per
    ``watchdog_log_throttle_seconds`` (30s default).

    This test drives ``watchdog.evaluate(classify_quiet=...)`` 1000
    times via the PUBLIC entry point and reaches the deferred-fire
    branch via PUBLIC behavior:

      1. ``record_subagent_work(now=0.0, description="phase-1")``
         seeds the ``subagent_progress_count=1`` and
         ``last_subagent_progress_at=0.0`` -- the watchdog's
         ``subagent_output`` channel then reports
         ``counter=1, age=5.1s`` at evaluate() time (>= 1.0s
         ``silent_subagent_seconds``).
      2. ``set_is_waiting_state(False)`` (public method) prevents
         the classifier from returning ``DUPLICATE_KILL``.
      3. ``_stale_subagent_corroborator`` returns
         ``scoped_child_active=True, alive_by=OS_DESCENDANT_ONLY_STALE_PROGRESS``
         -- required for the SUB-ceiling block to fire.
      4. ``_HelpersOnlyMonitor`` returns ``live_subagent_count()=0``
         -- the watchdog's ``subagent_liveness`` channel reports
         ``alive_by=None`` and ``can_defer=False``. Combined with the
         stale ``subagent_output`` channel, the
         ``_silent_subagent_path`` branch triggers and the classifier
         returns ``SILENT_SUBAGENT``.
      5. Each ``evaluate()`` after ``stuck_job_sub_ceiling_seconds``
         (5s) elapses calls ``_handle_waiting_branch`` which reaches
         the SUB-ceiling block at line 184; ``_gate_fire`` returns
         ``CONTINUE`` (deferred fire) and emits the DEBUG log
         "idle watchdog: silent subagent (deferred)
         reason=CHILDREN_PERSIST_TOO_LONG idle_elapsed=...s".

    The throttle holds: 1000 calls in the same 30s throttle window
    produce AT MOST 1 DEBUG log (initial transition; subsequent calls
    are suppressed by the coarse single-key throttle). The test
    asserts ``<= 2`` to tolerate the per-tuple throttle's refresh
    window (the canonical R6 spam-invariant matches the private-seam
    ``test_log_spam_throttle.py`` ceiling).

    The test uses NO private seams: no ``setattr`` on
    ``_classify_stuck_now``, no direct call to ``_gate_fire``, no
    read of any ``_last_*_log_at`` field. It proves the R6 invariant
    from the PUBLIC listener and loguru sink surfaces only.
    """
    _buf, log_records = captured_log_records
    captured_events: list[WaitingStatusEvent] = []

    def _listener(event: WaitingStatusEvent) -> None:
        captured_events.append(event)

    clock = FakeClock(start=0.0)
    watchdog = _build_deferred_fire_watchdog(
        listener=_listener,
        clock=clock,
    )

    # PUBLIC: ensure the classifier's first branch (is_waiting_state)
    # does not return DUPLICATE_KILL. The watchdog runs OUTSIDE a
    # pipeline wait state in this scenario. MUST be called BEFORE
    # ``record_invocation_start`` because the latter resets
    # ``_is_waiting_state`` to False anyway -- but calling it
    # explicitly documents the contract.
    watchdog.set_is_waiting_state(False)
    watchdog.record_invocation_start()
    # PUBLIC: seed the evidence summary so the StuckClassifier falls
    # through to the SILENT_SUBAGENT branch (stale subagent_output,
    # alive_by=None on subagent_liveness, no first-party fresh,
    # noop classify_quiet returns ACTIVE). MUST be called AFTER
    # ``record_invocation_start`` because the latter resets the
    # per-channel evidence counters and timestamps
    # (``_subagent_progress_count``, ``_last_subagent_progress_at``,
    # ``_last_subagent_progress_description``).
    watchdog.record_subagent_work(now=0.0, description="phase-1")

    # First evaluate() at t=3.0s enters the WAITING_ON_CHILD
    # branch (classify_quiet returns WAITING_ON_CHILD). The
    # SUB-ceiling block at line 184 is consulted but
    # ``current_run_elapsed=0`` and ``candidate_total=0`` so the
    # block does NOT fire yet. The branch emits the
    # ``WAITING_ON_CHILD deferral`` INFO log + ENTERED event.
    clock.advance(3.0)
    first_verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)
    assert first_verdict == WatchdogVerdict.WAITING_ON_CHILD, (
        f"first evaluate() MUST enter WAITING_ON_CHILD deferral; got {first_verdict!r}"
    )

    # Advance so ``current_run_elapsed >= stuck_job_sub_ceiling_seconds``.
    # The SUB-ceiling block at line 184 fires when
    # ``candidate_total >= stuck_job_sub_ceiling_seconds``. With
    # ``_waiting_on_child_started_at=3.0`` (from the first evaluate())
    # and ``_cumulative_waiting_on_child_seconds=0.0``, advancing the
    # clock to t=8.1 makes ``current_run_elapsed=5.1`` and
    # ``candidate_total=5.1 >= 5.0`` so the SUB-ceiling block fires
    # on every subsequent evaluate() call.
    clock.advance(5.1)

    # Drive 1000 evaluate() calls in the SAME 30s throttle window
    # (no further clock advance). Each call enters
    # ``_handle_waiting_branch``, reaches the SUB-ceiling block,
    # and calls ``self._gate_fire(...)`` -- the deferred-fire
    # branch that produced the original spam regression. The
    # classifier returns ``SILENT_SUBAGENT`` via the public
    # evidence summary; ``_gate_fire`` returns ``CONTINUE`` and the
    # throttle (``_maybe_log_any_deferred`` then
    # ``_maybe_log_deferred``) caps DEBUG emissions.
    # ``evaluate()`` propagates the gate's CONTINUE -- this is the
    # PUBLIC surface signature for a deferred-fire cycle. Before
    # the SUB-ceiling block fires, ``_handle_waiting_branch`` falls
    # through to the cadence gate which emits PROGRESS-kind
    # ``WaitingStatusEvent`` instances and returns
    # ``WAITING_ON_CHILD``. After the SUB-ceiling block fires, the
    # gate defers and ``_handle_waiting_branch`` returns
    # ``CONTINUE``. Either verdict proves the watchdog stayed in
    # deferral (no FIRE was emitted).
    for i in range(1000):
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)
        assert verdict in (
            WatchdogVerdict.WAITING_ON_CHILD,
            WatchdogVerdict.CONTINUE,
        ), f"evaluate() #{i} MUST stay in deferral (CONTINUE or WAITING_ON_CHILD); got {verdict!r}"

    # ASSERTION 1 (the headline R6 invariant): DEBUG records
    # matching the deferred-fire spam pattern are <= 2 per
    # throttle window. The private-seam pin test
    # ``test_log_spam_throttle.py::test_gate_fire_throttles_identical_deferred_emission``
    # asserts the same bound (one initial + one refresh); this
    # public-surface test proves the same invariant is observable
    # from the PUBLIC loguru sink filtered on
    # ``component='idle_watchdog'``.
    deferred_fire_records = [
        r for r in log_records if "silent subagent" in r and "children_persist_too_long" in r
    ]
    assert len(deferred_fire_records) <= 2, (
        f"R6 deferred-fire spam regression: got"
        f" {len(deferred_fire_records)} 'silent subagent"
        f" (deferred)' records for 1000 evaluate() calls in the"
        f" same throttle window; expected <= 2 (one initial"
        f" transition + one refresh). Records: {deferred_fire_records[:3]}"
    )

    # ASSERTION 2: the sink is REAL (not trivially empty). The
    # deferred-fire emission MUST have flowed through the sink
    # -- a zero-sink bypass (e.g. a future refactor that
    # silently renames the ``component`` bind) cannot pass this
    # test trivially. The classifier is configured to return
    # SILENT_SUBAGENT and ``_gate_fire`` MUST emit exactly one
    # DEBUG record on the initial transition.
    assert len(deferred_fire_records) >= 1, (
        f"loguru sink filtered on component='idle_watchdog' MUST"
        f" capture the deferred-fire DEBUG log emitted at"
        f" _gate.py:174; got {len(deferred_fire_records)} records"
        f" matching 'silent subagent' + 'children_persist_too_long'"
        f" (a zero-sink bypass means the bound is meaningless)."
    )

    # ASSERTION 3: the watchdog's PUBLIC ``last_fire_reason``
    # surface shows the classifier-kind label -- operators can
    # see WHY a would-be fire was deferred via the public
    # property (no setattr / no private read required).
    assert watchdog.last_deferred_kind == StuckKind.SILENT_SUBAGENT, (
        f"watchdog.last_deferred_kind (PUBLIC property) MUST be"
        f" SILENT_SUBAGENT after 1000 deferred-fire cycles; got"
        f" {watchdog.last_deferred_kind!r}"
    )

    # ASSERTION 4: PROGRESS-kind WaitingStatusEvent emissions are
    # also bounded by the cadence gate (``waiting_status_interval_seconds``
    # = 10_000.0s, so the cadence gate is closed for the entire
    # 1000-call cycle). This is the secondary R6 witness -- the
    # cadence gate and the deferred-fire throttle are two
    # distinct spam-suppression mechanisms and both must hold.
    progress_events = [e for e in captured_events if e.kind == WaitingStatusKind.PROGRESS]
    assert len(progress_events) <= 2, (
        f"R6 PROGRESS-event cadence MUST cap emissions to <= 2"
        f" per cadence window; got {len(progress_events)} PROGRESS"
        f" events across 1000 evaluate() calls."
    )

    # ASSERTION 5: ENTERED event fires EXACTLY once (on first
    # WAITING entry). This is a public-surface witness that the
    # WAITING branch was actually entered -- a missing ENTERED
    # would imply the watchdog never deferred, which would make
    # the throttle invariant trivially true (a zero-sink bypass).
    entered_events = [e for e in captured_events if e.kind == WaitingStatusKind.ENTERED]
    assert len(entered_events) == 1, (
        f"R6 ENTERED event MUST fire exactly once on first WAITING"
        f" entry; got {len(entered_events)} ENTERED events."
    )

    # ASSERTION 6: no HARD_STOP emission -- the deferred-fire
    # branch returns CONTINUE on every call (the gate's
    # CONTINUE response signals the SUB-ceiling block to stay
    # in deferral). HARD_STOP only fires when ``_gate_fire``
    # returns FIRE (the cumulative ceiling path or the
    # post-deferral fire path).
    hard_stop_events = [e for e in captured_events if e.kind == WaitingStatusKind.HARD_STOP]
    assert not hard_stop_events, (
        f"R6 deferred-fire branch MUST NOT emit HARD_STOP events"
        f" while _gate_fire returns CONTINUE (defer); got"
        f" {len(hard_stop_events)} HARD_STOP events."
    )


def test_log_spam_throttle_public_surface_deferred_fire_throttle_window(
    captured_log_records: tuple[io.StringIO, list[str]],
) -> None:
    """R6 secondary witness: throttle window refresh allows a second emission.

    The per-(fire_reason, deferred_kind) throttle plus the coarse
    single-key throttle allow ONE refresh emission per
    ``watchdog_log_throttle_seconds`` window per ``fire_reason``.
    With a small throttle window (0.05s) the test exercises the
    refresh boundary: 100 calls at t=5.1s (initial transition +
    throttled rest) and 100 more calls at t=5.2s (past the
    refresh window, ONE more emission). Total: <= 3 records
    across 200 calls in two throttle windows.

    This is the public-surface analogue of the private-seam
    ``test_log_spam_throttle.py::test_gate_fire_throttle_uses_configured_window``
    test and proves the throttle refresh boundary is observable
    from the PUBLIC loguru sink.
    """
    _buf, log_records = captured_log_records
    captured_events: list[WaitingStatusEvent] = []

    def _listener(event: WaitingStatusEvent) -> None:
        captured_events.append(event)

    clock = FakeClock(start=0.0)
    # Tight throttle window so the refresh boundary is reachable
    # in a single test. The 0.05s window allows ONE refresh
    # emission between two well-spaced batches.
    watchdog = _build_deferred_fire_watchdog(
        listener=_listener,
        clock=clock,
        watchdog_log_throttle_seconds=0.05,
    )

    watchdog.set_is_waiting_state(False)
    watchdog.record_invocation_start()
    watchdog.record_subagent_work(now=0.0, description="phase-1")
    clock.advance(3.0)
    watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)
    clock.advance(5.1)

    # Batch 1: 100 calls in the initial throttle window
    # (now=5.1s, no advance). Emits ONE initial deferred-fire
    # DEBUG record; the next 99 are throttled.
    for _ in range(100):
        watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)

    # Advance past the 0.05s throttle window to open the
    # refresh boundary.
    clock.advance(0.1)

    # Batch 2: 100 more calls in the new throttle window.
    # The first call emits ONE refresh DEBUG record; the
    # remaining 99 are throttled. Total across both batches:
    # <= 3 records (2 emissions + 1 potential per-tuple
    # refresh edge case).
    for _ in range(100):
        watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)

    deferred_fire_records = [
        r for r in log_records if "silent subagent" in r and "children_persist_too_long" in r
    ]
    assert len(deferred_fire_records) <= 3, (
        f"R6 throttle window 0.05s produced too many deferred-fire"
        f" emissions: got {len(deferred_fire_records)} records for"
        f" 200 calls in two throttle windows; expected <= 3"
        f" (initial + refresh + per-tuple edge case)."
    )
    assert len(deferred_fire_records) >= 1, (
        f"loguru sink MUST capture the deferred-fire DEBUG log;"
        f" got {len(deferred_fire_records)} records."
    )


def test_log_spam_throttle_public_surface_kind_cycle_via_public_surface(
    captured_log_records: tuple[io.StringIO, list[str]],
) -> None:
    """R6 deferred-kind cycle proof via PUBLIC surface.

    Coarse throttle holds across ``SILENT_SUBAGENT`` <-> ``DUPLICATE_KILL``.

    The PROMPT log showed ~10 DEBUG records/sec at ``_gate_fire:949``
    while a fire was deferred. The fix added a per-``(fire_reason,
    deferred_kind)`` throttle plus a COARSE single-key throttle keyed
    on ``fire_reason.value`` alone so the throttle holds even when
    the ``deferred_kind`` cycles between calls.

    Pre-fix the per-tuple throttle MISSED the duplicate emission
    whenever the ``deferred_kind`` changed (e.g.
    ``SILENT_SUBAGENT`` -> ``LOADING`` -> ``SILENT_SUBAGENT``) because
    the per-tuple key changed on every cycle. The coarse throttle
    solves this by keying on ``fire_reason.value`` alone, capping
    emissions to at most one DEBUG record per ``watchdog_log_throttle_seconds``
    per ``fire_reason`` regardless of how the ``deferred_kind`` cycles.

    This test drives ``watchdog.evaluate(classify_quiet=...)`` 1000
    times in the same FakeClock second, alternating the deferred-kind
    value between ``SILENT_SUBAGENT`` and ``DUPLICATE_KILL`` via the
    PUBLIC ``watchdog.set_is_waiting_state(bool)`` method -- the
    pipeline-facing surface that the run loop uses to mirror
    ``state.is_waiting_state``. No ``setattr`` on
    ``_classify_stuck_now`` and no direct call to ``_gate_fire``; the
    classifier's verdict is driven entirely by the public state.

    The cycle scenario:

      * ``set_is_waiting_state(False)``: classifier falls through to
        branch 7 (``SILENT_SUBAGENT``) because the
        ``subagent_output`` channel is seeded (counter=1, age=5.1s,
        5.1 >= ``silent_subagent_seconds=1.0``) AND
        ``subagent_liveness`` has ``alive_by=None`` (the
        ``_HelpersOnlyMonitor`` returns ``live_subagent_count()=0``
        so the process-monitor live-subagent signal is absent). Gate
        emits the ``idle watchdog: silent subagent (deferred) ...``
        DEBUG record.
      * ``set_is_waiting_state(True)``: classifier returns
        ``DUPLICATE_KILL`` immediately on branch 1 (the highest-
        priority branch). Gate emits the
        ``idle watchdog: deferred fire reason=CHILDREN_PERSIST_TOO_LONG
        kind=duplicate_kill ...`` DEBUG record.

    Both deferred-fire DEBUG records share the same
    ``fire_reason`` key (``CHILDREN_PERSIST_TOO_LONG``); the coarse
    single-key throttle (``_maybe_log_any_deferred``) suppresses
    emissions after the first one in the same throttle window
    regardless of which ``deferred_kind`` the classifier returned.

    The test asserts ``len(deferred_fire_records) <= 2`` (one initial
    transition + one per-tuple refresh edge case -- the same bound the
    private-seam
    ``test_log_spam_throttle.py::test_coarse_single_key_throttle_caps_emissions_across_kind_cycles``
    test asserts). Pre-fix the count is ~500 because the per-tuple
    throttle MISSED on every cycle (different key per call); post-fix
    the coarse throttle caps emissions to <= 2 records.

    The ``>= 1`` lower bound guards against a zero-sink bypass: if
    the ``component='idle_watchdog'`` loguru bind is silently
    renamed in a future refactor, the sink would capture zero
    records and the bound would lose its meaning.
    """
    _buf, log_records = captured_log_records
    captured_events: list[WaitingStatusEvent] = []

    def _listener(event: WaitingStatusEvent) -> None:
        captured_events.append(event)

    clock = FakeClock(start=0.0)
    watchdog = _build_deferred_fire_watchdog(
        listener=_listener,
        clock=clock,
    )

    watchdog.set_is_waiting_state(False)
    watchdog.record_invocation_start()
    watchdog.record_subagent_work(now=0.0, description="phase-1")
    clock.advance(3.0)
    watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)
    clock.advance(5.1)

    # Drive 1000 evaluate() calls in the SAME 30s throttle window
    # (no further clock advance). Alternate ``set_is_waiting_state``
    # between False (classifier falls through to SILENT_SUBAGENT
    # via branch 7) and True (classifier returns DUPLICATE_KILL on
    # branch 1 immediately). The gate's classifier call sees a
    # different ``deferred_kind`` on every other call -- exactly the
    # ``SILENT_SUBAGENT`` -> ``DUPLICATE_KILL`` -> ``SILENT_SUBAGENT``
    # -> ... cycle the coarse single-key throttle is designed to
    # suppress. Without the coarse throttle the per-tuple throttle
    # would MISS on every other call (different
    # ``(fire_reason, deferred_kind)`` tuple) and the gate would log
    # ~500 DEBUG records.
    for i in range(1000):
        # PUBLIC: drive the classifier's ``is_waiting_state`` input
        # via the canonical run-loop-facing method. Even i ->
        # is_waiting_state=False (classifier branch 7 SILENT_SUBAGENT),
        # odd i -> is_waiting_state=True (classifier branch 1
        # DUPLICATE_KILL).
        watchdog.set_is_waiting_state(i % 2 == 1)
        verdict = watchdog.evaluate(classify_quiet=lambda: AgentExecutionState.WAITING_ON_CHILD)
        assert verdict in (
            WatchdogVerdict.WAITING_ON_CHILD,
            WatchdogVerdict.CONTINUE,
        ), f"evaluate() #{i} MUST stay in deferral (CONTINUE or WAITING_ON_CHILD); got {verdict!r}"

    # ASSERTION 1 (the headline R6 invariant across kind-cycles):
    # the coarse single-key throttle MUST cap DEBUG emissions to
    # <= 2 records per ``watchdog_log_throttle_seconds`` per
    # ``fire_reason`` REGARDLESS of how the ``deferred_kind``
    # cycles. The filter captures BOTH the
    # ``silent subagent (deferred)`` log (SILENT_SUBAGENT branch
    # of ``_gate_fire``) AND the generic ``deferred fire reason=...
    # kind=...`` log (DUPLICATE_KILL branch -- and any other
    # non-STUCK, non-SILENT_SUBAGENT deferred kind).
    deferred_fire_records = [
        r
        for r in log_records
        if (("silent subagent" in r or "deferred fire" in r) and "children_persist_too_long" in r)
    ]
    assert len(deferred_fire_records) <= 2, (
        f"R6 coarse single-key throttle MUST cap emissions across"
        f" SILENT_SUBAGENT <-> DUPLICATE_KILL kind-cycles; got"
        f" {len(deferred_fire_records)} deferred-fire DEBUG records"
        f" for 1000 evaluate() calls in the same throttle window."
        f" Records: {deferred_fire_records[:3]}"
    )

    # ASSERTION 2: the sink is REAL (not trivially empty). The
    # FIRST evaluate() with a new ``deferred_kind`` MUST emit a
    # DEBUG record (the initial transition is never throttled).
    # A zero-sink bypass would silently capture zero records and
    # the upper bound would be vacuously satisfied.
    assert len(deferred_fire_records) >= 1, (
        f"loguru sink filtered on component='idle_watchdog' MUST"
        f" capture the first deferred-fire DEBUG log; got"
        f" {len(deferred_fire_records)} records (a zero-sink bypass"
        f" would make the throttle bound meaningless)."
    )

    # ASSERTION 3: the watchdog's PUBLIC ``last_deferred_kind``
    # surface reports BOTH kinds across the cycle (operators can
    # see WHY each fire was deferred even when the coarse throttle
    # suppressed the log emission -- the kind label is preserved
    # on ``_last_deferred_kind`` regardless of throttle state).
    # The 1000-call cycle ENDS with i=999 (odd), so
    # ``is_waiting_state=True`` was set just before the last
    # evaluate(); the classifier returned DUPLICATE_KILL on that
    # last call.
    assert watchdog.last_deferred_kind in (
        StuckKind.SILENT_SUBAGENT,
        StuckKind.DUPLICATE_KILL,
    ), (
        f"watchdog.last_deferred_kind (PUBLIC property) MUST be"
        f" SILENT_SUBAGENT or DUPLICATE_KILL after 1000 cycle"
        f" iterations; got {watchdog.last_deferred_kind!r}"
    )

    # ASSERTION 4: PROGRESS-kind WaitingStatusEvent emissions are
    # also bounded by the cadence gate
    # (``waiting_status_interval_seconds`` = 10_000.0s, so the
    # cadence gate is closed for the entire 1000-call cycle).
    # The throttle on deferred-fire DEBUG logs and the cadence on
    # WaitingStatusEvent emissions are two distinct spam-suppression
    # mechanisms; both must hold for R6.
    progress_events = [e for e in captured_events if e.kind == WaitingStatusKind.PROGRESS]
    assert len(progress_events) <= 2, (
        f"R6 PROGRESS-event cadence MUST cap emissions to <= 2"
        f" per cadence window; got {len(progress_events)} PROGRESS"
        f" events across 1000 evaluate() calls."
    )

    # ASSERTION 5: ENTERED event fires EXACTLY once (on first
    # WAITING entry). This is a public-surface witness that the
    # WAITING branch was actually entered -- a missing ENTERED
    # would imply the watchdog never deferred, which would make
    # the throttle invariant trivially true (a zero-sink bypass).
    entered_events = [e for e in captured_events if e.kind == WaitingStatusKind.ENTERED]
    assert len(entered_events) == 1, (
        f"R6 ENTERED event MUST fire exactly once on first WAITING"
        f" entry; got {len(entered_events)} ENTERED events."
    )

    # ASSERTION 6: no HARD_STOP emission -- the deferred-fire
    # branch returns CONTINUE on every call (the gate's CONTINUE
    # response signals the SUB-ceiling block to stay in deferral).
    # HARD_STOP only fires when ``_gate_fire`` returns FIRE (the
    # cumulative ceiling path or the post-deferral fire path),
    # which never happens in this configuration.
    hard_stop_events = [e for e in captured_events if e.kind == WaitingStatusKind.HARD_STOP]
    assert not hard_stop_events, (
        f"R6 deferred-fire branch MUST NOT emit HARD_STOP events"
        f" while _gate_fire returns CONTINUE (defer); got"
        f" {len(hard_stop_events)} HARD_STOP events."
    )
