"""wt-047-stall-label: the watchdog owns and publishes stall-state transitions.

The status bar's ``STALLED`` label was derived from a display-side 30s
gap between ``last_activity_monotonic`` and ``now_monotonic``, which
drifted from the watchdog's own stall assessment (the watchdog could
report a healthy session while the bar said STALLED, or vice versa).

The single source of truth is now the watchdog. This module pins the
transition contract: the watchdog emits one ``STALLED`` event on entry
into a stall, one ``STALL_RESUMED`` event on exit, and never emits
duplicates between transitions.

The trigger points are:

- STALLED:
  * SUSPECTED_FROZEN emission in ``_waiting_branch.handle_waiting_branch``
  * HARD_STOP emission in ``_waiting_branch.handle_waiting_branch``
    (both the stuck_job_sub_ceiling and cumulative ceiling branches)
  * FIRE verdict returned from ``evaluate()`` for non-absolute reasons
  * gate-deferral at the SILENT_SUBAGENT kind (the gate fires here, but
    the configured ``warn`` log is the diagnostic surface; the
    explicit ``STALLED`` event is emitted at the moment the gate
    allows the FIRE)

- STALL_RESUMED:
  * ``record_activity()`` / ``_reset_idle_baseline()`` /
    ``record_invocation_start()``
  * waiting ``EXITED`` transition in ``_accumulate_waiting_run``
  * a later tick where the SILENT_SUBAGENT deferral no longer holds
    while no other stall trigger is active

All tests use ``FakeClock`` and a capturing ``WaitingStatusListener``
to drive the watchdog deterministically. No real sleep, no real
subprocess, no real network.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    AliveBy,
    CorroborationSnapshot,
    IdleWatchdog,
    TimeoutPolicy,
    WaitingCorroborator,
    WaitingStatusEvent,
    WaitingStatusKind,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.idle_watchdog._stuck_classifier import StuckKind
from ralph.agents.timeout_clock import FakeClock

if TYPE_CHECKING:
    from ralph.agents.idle_watchdog.waiting_status_event import WaitingStatusListener


def _events(captured: list[WaitingStatusEvent]) -> list[WaitingStatusEvent]:
    """Return the captured events list typed for assertion helpers."""
    return captured


def _stall_state(watchdog: IdleWatchdog) -> bool:
    """Return the watchdog's current stall state via the public property."""
    return bool(watchdog.is_stalled)


def _make_watchdog(
    *,
    listener: WaitingStatusListener | None = None,
    idle_timeout_seconds: float | None = 60.0,
    no_output_at_start_seconds: float | None = 30.0,
    drain_window_seconds: float = 0.0,
    max_waiting_on_child_seconds: float = 1800.0,
    max_session_seconds: float | None = None,
    no_progress_quiet_seconds: float | None = None,
    watchdog_log_throttle_seconds: float = 30.0,
    activity_evidence_ttl_seconds: float | None = 180.0,
    suspect_waiting_on_child_seconds: float | None = None,
    max_waiting_on_child_no_progress_seconds: float | None = None,
    corroborator: object | None = None,
) -> tuple[IdleWatchdog, FakeClock]:
    """Construct a watchdog with the canonical test policy.

    Each ``TimeoutPolicy`` field accepted as a keyword argument
    preserves the typed call site (``TimeoutPolicy(**kwargs)`` keeps
    every override narrowly typed without ``cast`` / ``type: ignore``
    suppression). Only the fields exercised by
    ``test_stall_status_events`` accept overrides here; every other
    field falls back to the dataclass default. The default
    ``idle_timeout`` is sized so the post-tool-result stall helper
    can drive ``STALLED_AFTER_TOOL_RESULT`` deterministically without
    needing real time. The ``stuck_job_sub_ceiling_seconds`` is left
    default so the cumulative-ceiling branch can be exercised.

    The ``corroborator`` parameter is forwarded into
    ``IdleWatchdog.__init__`` so SUSPECTED_FROZEN tests can drive the
    WAITING_ON_CHILD branch through ``evaluate()`` (the SUSPECTED
    threshold is computed against the corroborator's ``alive_by``).

    The ``max_waiting_on_child_no_progress_seconds`` parameter is
    needed when a test narrows ``max_waiting_on_child_seconds`` below
    the dataclass default of 600.0 -- the cross-field validator
    rejects any no-progress ceiling that exceeds the main ceiling.
    Tests that keep the default ``max_waiting_on_child_seconds`` of
    1800.0 do not need to override it.
    """
    clock = FakeClock(start=0.0)
    # If the no-progress ceiling is unset but the test narrows
    # ``max_waiting_on_child_seconds`` below the dataclass default
    # of 600.0, mirror the test's narrower ceiling so the validator
    # is satisfied without forcing the caller to spell out the
    # secondary knob. Same trick for the
    # ``os_descendant_only_ceiling_seconds`` (default 300.0) and the
    # ``stuck_job_sub_ceiling_seconds`` (default 600.0) -- they
    # must all be <= ``max_waiting_on_child_seconds``.
    if max_waiting_on_child_no_progress_seconds is None and max_waiting_on_child_seconds < 600.0:
        max_waiting_on_child_no_progress_seconds = max_waiting_on_child_seconds
    if max_waiting_on_child_seconds < 300.0:
        os_descendant_only_ceiling_seconds: float | None = max_waiting_on_child_seconds
        # The OS-descendant-only suspect threshold (default 60.0) must
        # be strictly less than the OS-descendant-only ceiling. When
        # the test narrows the ceiling below 60.0, mirror it.
        os_descendant_only_suspect_seconds: float | None = max(
            suspect_waiting_on_child_seconds or 1.0,
            max_waiting_on_child_seconds / 2.0,
        )
    else:
        os_descendant_only_ceiling_seconds = None
        os_descendant_only_suspect_seconds = None
    if max_waiting_on_child_seconds < 600.0:
        stuck_job_sub_ceiling_seconds: float | None = max_waiting_on_child_seconds
    else:
        stuck_job_sub_ceiling_seconds = None
    policy = TimeoutPolicy(
        idle_timeout_seconds=idle_timeout_seconds,
        drain_window_seconds=drain_window_seconds,
        max_waiting_on_child_seconds=max_waiting_on_child_seconds,
        max_session_seconds=max_session_seconds,
        no_output_at_start_seconds=no_output_at_start_seconds,
        no_progress_quiet_seconds=no_progress_quiet_seconds,
        watchdog_log_throttle_seconds=watchdog_log_throttle_seconds,
        activity_evidence_ttl_seconds=activity_evidence_ttl_seconds,
        suspect_waiting_on_child_seconds=suspect_waiting_on_child_seconds,
        max_waiting_on_child_no_progress_seconds=max_waiting_on_child_no_progress_seconds,
        os_descendant_only_ceiling_seconds=os_descendant_only_ceiling_seconds,
        os_descendant_only_suspect_seconds=os_descendant_only_suspect_seconds,
        stuck_job_sub_ceiling_seconds=stuck_job_sub_ceiling_seconds,
    )
    return (
        IdleWatchdog(policy, clock, listener=listener, corroborator=corroborator),
        clock,
    )


def _classifier_to_stuck_now(
    watchdog: IdleWatchdog,
    *,
    reason: WatchdogFireReason = WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG,
) -> None:
    """Force ``_classify_stuck_now`` to return STUCK so the gate fires.

    The classifier is pure; monkey-patching it directly is the cleanest
    seam for these tests. See the same pattern in
    ``tests/agents/idle_watchdog/test_log_spam_throttle.py``.
    """
    _attr = "_classify_stuck_now"

    def _stuck_now(
        *,
        now: float,
        idle_elapsed: float,
        corroboration: CorroborationSnapshot | None = None,
    ) -> StuckKind:
        return StuckKind.STUCK

    setattr(watchdog, _attr, _stuck_now)


# ---------------------------------------------------------------------------
# is_stalled + _set_stall transition semantics
# ---------------------------------------------------------------------------


def test_is_stalled_initially_false() -> None:
    """A fresh watchdog is NOT in a stall."""
    watchdog, _clock = _make_watchdog()
    assert _stall_state(watchdog) is False


def test_set_stall_emits_stalled_transition_only_once() -> None:
    """A single ``_set_stall(active=True)`` call emits one STALLED event."""
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _make_watchdog(listener=captured.append)
    watchdog._set_stall(active=True, now=100.0, idle_elapsed=100.0)
    stalled_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled_events) == 1
    assert _stall_state(watchdog) is True


def test_set_stall_repeated_active_emits_no_duplicates() -> None:
    """Repeated ``_set_stall(active=True)`` emits no duplicate STALLED events."""
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _make_watchdog(listener=captured.append)
    for _ in range(10):
        watchdog._set_stall(active=True, now=100.0, idle_elapsed=100.0)
    stalled_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled_events) == 1, (
        f"STALLED must be emitted only on transition; got {len(stalled_events)}"
    )


def test_set_stall_toggle_emits_stall_resumed_once() -> None:
    """Toggling from active=True to active=False emits exactly one STALL_RESUMED."""
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _make_watchdog(listener=captured.append)
    watchdog._set_stall(active=True, now=100.0, idle_elapsed=100.0)
    for _ in range(10):
        watchdog._set_stall(active=False, now=200.0, idle_elapsed=0.0)
    resumed_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALL_RESUMED]
    assert len(resumed_events) == 1
    assert _stall_state(watchdog) is False


def test_set_stall_idempotent_false_emits_no_event() -> None:
    """Active=False on a fresh watchdog emits no STALL_RESUMED."""
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _make_watchdog(listener=captured.append)
    watchdog._set_stall(active=False, now=100.0, idle_elapsed=0.0)
    assert _events(captured) == []


# ---------------------------------------------------------------------------
# SUSPECTED_FROZEN emission site
# ---------------------------------------------------------------------------


def _fresh_progress_corroborator() -> WaitingCorroborator:
    """Return a corroborator that always reports a fresh-progress child.

    ``FRESH_PROGRESS`` is the cleanest live-child signal for these
    tests because it is excluded from the watchdog's
    ``_STUCK_ALIVE_BY_VALUES`` and ``_NON_PROGRESS_ALIVE_BY_VALUES``
    sets, so neither the stuck-job sub-ceiling nor the
    no-progress ceiling engages. The SUSPECTED_FROZEN emission site
    then fires on the standard suspect threshold without competing
    HARD_STOP branches.
    """

    def _corr() -> CorroborationSnapshot:
        return CorroborationSnapshot(
            alive_by=AliveBy.FRESH_PROGRESS,
            scoped_child_active=True,
            scoped_child_count=1,
        )

    return _corr


def test_suspected_frozen_emits_stalled_event() -> None:
    """The SUSPECTED_FROZEN emission site drives a single STALLED transition.

    Drives the actual production path: the first ``evaluate()`` with
    WAITING_ON_CHILD enters the deferral branch and emits ENTERED;
    the second ``evaluate()`` after the clock has advanced past the
    suspect threshold crosses the SUSPECTED_FROZEN line and emits
    one SUSPECTED_FROZEN plus one STALLED. A third ``evaluate()``
    on the same tick must NOT emit a duplicate STALLED (the
    ``_set_stall`` helper dedupes by the runtime flag).

    The previous version of this test only called ``_set_stall``
    directly and never drove the SUSPECTED_FROZEN production site
    (DA-002: it pinned the helper, not the contract). The new
    version drives the SUSPECTED branch through ``evaluate()`` and
    inspects the capturing listener.
    """
    captured: list[WaitingStatusEvent] = []
    watchdog, clock = _make_watchdog(
        listener=captured.append,
        idle_timeout_seconds=10.0,
        max_waiting_on_child_seconds=30.0,
        suspect_waiting_on_child_seconds=5.0,
        no_progress_quiet_seconds=None,
        corroborator=_fresh_progress_corroborator(),
    )

    def _waiting() -> AgentExecutionState:
        return AgentExecutionState.WAITING_ON_CHILD

    # First evaluate: enter WAITING_ON_CHILD, emit ENTERED.
    clock.advance(11.0)
    watchdog.evaluate(classify_quiet=_waiting)
    assert _stall_state(watchdog) is False
    assert any(e.kind == WaitingStatusKind.ENTERED for e in _events(captured))

    # Second evaluate after crossing the suspect threshold (5s).
    clock.advance(6.0)
    watchdog.evaluate(classify_quiet=_waiting)

    stalled_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    suspect_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.SUSPECTED_FROZEN]
    assert len(suspect_events) == 1, (
        f"Expected exactly one SUSPECTED_FROZEN event, got {len(suspect_events)}: "
        f"{[e.kind for e in _events(captured)]}"
    )
    assert len(stalled_events) == 1, (
        f"Expected exactly one STALLED event paired with the SUSPECTED_FROZEN transition, "
        f"got {len(stalled_events)}: {[e.kind for e in _events(captured)]}"
    )
    assert _stall_state(watchdog) is True

    # Third evaluate on the same tick: NO new STALLED, NO new SUSPECTED.
    # SUSPECTED_FROZEN is gated by ``_suspicion_announced_for_run``; the
    # STALLED transition is gated by ``_stall_active``. Both must dedupe.
    watchdog.evaluate(classify_quiet=_waiting)
    stalled_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    suspect_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.SUSPECTED_FROZEN]
    assert len(stalled_events) == 1
    assert len(suspect_events) == 1


# ---------------------------------------------------------------------------
# FIRE verdict
# ---------------------------------------------------------------------------


def test_fire_verdict_emits_stalled_event() -> None:
    """A FIRE verdict (non-absolute reason) emits one STALLED listener event.

    The captured ``WaitingStatusListener`` is the contract surface the
    status bar subscribes to. The previous version of this test
    only asserted ``is_stalled`` and ``verdict == FIRE`` -- it never
    inspected the listener, so a regression that emitted a STALLED
    on the gate path without firing the listener would have
    silently passed. Drive the production ``evaluate()`` path with
    a capturing listener, assert exactly one ``WaitingStatusKind.STALLED``
    event, and repeat ``evaluate()`` to confirm the dedupe.
    """
    captured: list[WaitingStatusEvent] = []
    # Override the policy via the constructor so the frozen dataclass
    # is constructed with drain_window_seconds=0 (the active branch
    # fires NO_OUTPUT_DEADLINE immediately at the deadline).
    watchdog, _clock = _make_watchdog(
        listener=captured.append,
        drain_window_seconds=0.0,
    )
    # Force the gate to allow the fire (STUCK kind).
    _classifier_to_stuck_now(watchdog)

    # Move the clock past the idle timeout.
    _clock.advance(61.0)
    # classify_quiet returns ACTIVE; the active branch fires.
    verdict = watchdog.evaluate(lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.FIRE
    # FIRING implies STALLED state.
    assert _stall_state(watchdog) is True

    # The capturing listener MUST have received exactly one STALLED
    # transition event (DA-001: the listener is the contract surface
    # the status bar subscribes to, not just the internal flag).
    stalled_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled_events) == 1, (
        f"Expected exactly one STALLED event on FIRE; got {len(stalled_events)}: "
        f"{[e.kind for e in _events(captured)]}"
    )

    # A second evaluate on the same tick MUST NOT emit a duplicate
    # STALLED event (the _set_stall helper dedupes by the runtime flag).
    watchdog.evaluate(lambda: AgentExecutionState.ACTIVE)
    stalled_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled_events) == 1, (
        f"Repeated evaluate() must NOT emit duplicate STALLED events; got {len(stalled_events)}"
    )


def test_silent_subagent_emits_stalled_event() -> None:
    """A SILENT_SUBAGENT gate verdict emits one STALLED listener event.

    The SILENT_SUBAGENT branch of the classifier is a post-mortem
    LABEL, not a veto: the gate fires when the branch matches (no
    live child, stale subagent evidence). The fire path is
    ``_gate_fire -> StuckKind.SILENT_SUBAGENT -> _set_stall(active=True)``.
    The status bar subscribes to the WaitingStatusListener, so the
    STALLED transition MUST surface as a captured event -- not just
    flip the internal ``_stall_active`` flag.

    DA-001 fix: the previous coverage pinned the gate verdict but
    never inspected the listener. This test wires the production
    listener through ``_gate_fire`` and asserts the captured STALLED.
    """
    captured: list[WaitingStatusEvent] = []
    watchdog, clock = _make_watchdog(listener=captured.append)

    # Patch _classify_stuck_now to return SILENT_SUBAGENT deterministically.
    _attr = "_classify_stuck_now"

    def _silent_subagent_now(
        *,
        now: float,
        idle_elapsed: float,
        corroboration: CorroborationSnapshot | None = None,
    ) -> StuckKind:
        return StuckKind.SILENT_SUBAGENT

    setattr(watchdog, _attr, _silent_subagent_now)

    # Drive _gate_fire directly. The SILENT_SUBAGENT branch must
    # return FIRE AND emit exactly one STALLED listener event.
    _now = clock.monotonic() + 181.0
    gate_verdict = watchdog._gate_fire(
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        now=_now,
        idle_elapsed=181.0,
    )
    assert gate_verdict == WatchdogVerdict.FIRE, (
        f"SILENT_SUBAGENT must FIRE (the kind is a post-mortem LABEL, not a veto); "
        f"got {gate_verdict}"
    )

    stalled_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled_events) == 1, (
        f"Expected exactly one STALLED listener event on SILENT_SUBAGENT gate fire; "
        f"got {len(stalled_events)}: {[e.kind for e in _events(captured)]}"
    )
    assert _stall_state(watchdog) is True

    # A second _gate_fire on the same tick MUST NOT emit a duplicate.
    gate_verdict = watchdog._gate_fire(
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        now=_now,
        idle_elapsed=181.0,
    )
    assert gate_verdict == WatchdogVerdict.FIRE
    stalled_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled_events) == 1, (
        f"Repeated _gate_fire must NOT emit duplicate STALLED events; got {len(stalled_events)}"
    )


def test_fire_session_ceiling_emits_stalled_event() -> None:
    """A SESSION_CEILING_EXCEEDED FIRE emits exactly one STALLED transition.

    DA-001 contract: the SESSION_CEILING_EXCEEDED bypass path inside
    ``_gate_fire`` (``_gate.py:142``) transitions the runtime stall
    flag via ``_set_stall(active=True, ...)`` BEFORE returning FIRE.
    The watchdog is the sole owner of the ``STALLED`` label, and a
    session that hit the operator-set cap is also a stalled run from
    the operator's perspective (the cap fired because the run was
    alive but un-killable by every other rule). The Status Bar must
    surface the same stall signal here as for a STUCK classifier
    verdict or a SILENT_SUBAGENT fire.

    The previous version of this test asserted the OPPOSITE (no
    STALLED event, ``is_stalled is False``) because the SESSION_CEILING
    bypass path returned FIRE without calling ``_set_stall`` -- the
    DA-001 gap. The fix flips the bypass path to transition the
    runtime flag; the test now pins the listener contract and the
    runtime flag.

    Repeated ``evaluate()`` calls on the same tick MUST NOT emit a
    duplicate STALLED event (``_set_stall`` is idempotent on the
    runtime flag).
    """
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _make_watchdog(
        listener=captured.append,
        max_session_seconds=60.0,
        idle_timeout_seconds=30.0,
        max_waiting_on_child_seconds=99999.0,
        no_output_at_start_seconds=None,
    )
    _clock.advance(61.0)
    verdict = watchdog.evaluate(lambda: AgentExecutionState.ACTIVE)
    assert verdict == WatchdogVerdict.FIRE
    assert watchdog.last_fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED
    stalled_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled_events) == 1, (
        f"Expected exactly one STALLED listener event on SESSION_CEILING_EXCEEDED FIRE; "
        f"got {len(stalled_events)}: {[e.kind for e in _events(captured)]}"
    )
    assert _stall_state(watchdog) is True, (
        "SESSION_CEILING_EXCEEDED FIRE MUST transition the runtime stall flag; "
        "watchdog is the sole owner of the STALLED label (DA-001)."
    )

    # A second evaluate() on the same tick MUST NOT emit a duplicate
    # STALLED event (the _set_stall helper dedupes by the runtime flag).
    watchdog.evaluate(lambda: AgentExecutionState.ACTIVE)
    stalled_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled_events) == 1, (
        f"Repeated evaluate() on SESSION_CEILING_EXCEEDED MUST NOT emit duplicate "
        f"STALLED events; got {len(stalled_events)}"
    )


# ---------------------------------------------------------------------------
# Stall-OFF triggers: record_activity / record_invocation_start
# ---------------------------------------------------------------------------


def test_record_activity_emits_stall_resumed() -> None:
    """``record_activity`` clears the stall state and emits STALL_RESUMED."""
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _make_watchdog(listener=captured.append)
    watchdog._set_stall(active=True, now=100.0, idle_elapsed=100.0)
    assert _stall_state(watchdog) is True
    # Drain prior events.
    captured.clear()
    watchdog.record_activity()
    resumed_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALL_RESUMED]
    assert len(resumed_events) == 1
    assert _stall_state(watchdog) is False


def test_record_invocation_start_emits_stall_resumed() -> None:
    """``record_invocation_start`` clears the stall state and emits STALL_RESUMED."""
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _make_watchdog(listener=captured.append)
    watchdog._set_stall(active=True, now=100.0, idle_elapsed=100.0)
    captured.clear()
    watchdog.record_invocation_start()
    resumed_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALL_RESUMED]
    assert len(resumed_events) == 1
    assert _stall_state(watchdog) is False


# ---------------------------------------------------------------------------
# Stall-OFF trigger: waiting EXITED transition
# ---------------------------------------------------------------------------


def test_accumulate_waiting_run_emits_stall_resumed() -> None:
    """Transitioning out of WAITING (EXITED) emits STALL_RESUMED."""
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _make_watchdog(listener=captured.append)
    # Force the watchdog into a WAITING_ON_CHILD run.
    watchdog._waiting_on_child_started_at = 100.0
    watchdog._set_stall(active=True, now=100.0, idle_elapsed=100.0)
    captured.clear()
    watchdog._accumulate_waiting_run(200.0)
    resumed_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALL_RESUMED]
    assert len(resumed_events) == 1
    assert _stall_state(watchdog) is False


# ---------------------------------------------------------------------------
# Transition only on entry / exit (no per-tick spam)
# ---------------------------------------------------------------------------


def test_no_stall_event_emitted_when_already_idle() -> None:
    """Idle activity calls without a prior stall do NOT emit STALL_RESUMED."""
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _make_watchdog(listener=captured.append)
    # Fresh watchdog: no stall.
    watchdog.record_activity()
    watchdog.record_activity()
    resumed_events = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALL_RESUMED]
    assert len(resumed_events) == 0


def test_stall_oscillation_emits_only_on_transitions() -> None:
    """Stall toggling across many ticks emits only on transitions."""
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _make_watchdog(listener=captured.append)
    # 5 transitions.
    for i in range(5):
        watchdog._set_stall(active=True, now=float(i * 100), idle_elapsed=100.0)
        watchdog._set_stall(active=False, now=float(i * 100 + 50), idle_elapsed=0.0)
    # Then 100 ticks of repeated STALLED.
    for _ in range(100):
        watchdog._set_stall(active=True, now=10_000.0, idle_elapsed=100.0)
    # Then 100 ticks of repeated STALL_RESUMED.
    for _ in range(100):
        watchdog._set_stall(active=False, now=10_500.0, idle_elapsed=0.0)
    stalled = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    resumed = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALL_RESUMED]
    # 5 STALLED + 1 extra (the last 100-tick burst counts once) = 6
    # 5 STALL_RESUMED + 1 extra (the last 100-tick burst counts once) = 6
    assert len(stalled) == 6, (
        f"Expected exactly 6 STALLED events (5 transitions + 1 final), got {len(stalled)}"
    )
    assert len(resumed) == 6, (
        f"Expected exactly 6 STALL_RESUMED events (5 transitions + 1 final), got {len(resumed)}"
    )


# ---------------------------------------------------------------------------
# Public surface: is_stalled property
# ---------------------------------------------------------------------------


def test_is_stalled_property_reflects_internal_state() -> None:
    """The public ``is_stalled`` property mirrors the watchdog's internal state."""
    watchdog, _clock = _make_watchdog()
    assert watchdog.is_stalled is False
    watchdog._set_stall(active=True, now=100.0, idle_elapsed=100.0)
    assert watchdog.is_stalled is True
    watchdog._set_stall(active=False, now=200.0, idle_elapsed=0.0)
    assert watchdog.is_stalled is False


# ---------------------------------------------------------------------------
# DA-001 regression: gate deferral must clear a prior SILENT_SUBAGENT stall.
# ---------------------------------------------------------------------------


def test_gate_deferral_clears_silent_subagent_stall_on_non_stuck_tick() -> None:
    """A later non-stuck tick clears a prior SILENT_SUBAGENT stall.

    DA-001 contract: when the gate fires (returns ``WatchdogVerdict.FIRE``)
    on a ``StuckKind.SILENT_SUBAGENT`` verdict and the next tick's
    classifier no longer returns ``SILENT_SUBAGENT`` (e.g. the
    corroborator now reports a non-stuck kind like ``LOADING``), the
    gate's deferral path MUST clear the stall flag. Without this
    transition-out, the Status Bar would stay ``STALLED`` forever
    after a transient subagent silence even though the watchdog's
    own classifier is reporting a healthy session.

    The captured ``WaitingStatusListener`` is the contract surface
    the status bar subscribes to; both the ``STALLED`` transition
    on entry and the ``STALL_RESUMED`` transition on exit must be
    observable as listener events (not just internal flag flips).
    """
    captured: list[WaitingStatusEvent] = []
    watchdog, clock = _make_watchdog(listener=captured.append)

    # Patch _classify_stuck_now so we can drive the SILENT_SUBAGENT
    # transition followed by a non-stuck kind deterministically.
    sequence: list[StuckKind] = [StuckKind.SILENT_SUBAGENT, StuckKind.LOADING]
    _attr = "_classify_stuck_now"

    def _sequence_now(
        *,
        now: float,
        idle_elapsed: float,
        corroboration: CorroborationSnapshot | None = None,
    ) -> StuckKind:
        if sequence:
            return sequence.pop(0)
        return StuckKind.LOADING

    setattr(watchdog, _attr, _sequence_now)

    # Tick 1: SILENT_SUBAGENT -> gate fires + sets stall.
    first_now = clock.monotonic() + 181.0
    first_verdict = watchdog._gate_fire(
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        now=first_now,
        idle_elapsed=181.0,
    )
    assert first_verdict == WatchdogVerdict.FIRE
    stalled_after_first = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled_after_first) == 1
    assert _stall_state(watchdog) is True

    # Tick 2: classifier returns LOADING (not SILENT_SUBAGENT) -> gate
    # defers (CONTINUE). The deferral path must clear the stall flag
    # and emit exactly one STALL_RESUMED transition.
    second_now = clock.monotonic() + 1.0
    second_verdict = watchdog._gate_fire(
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        now=second_now,
        idle_elapsed=1.0,
    )
    assert second_verdict == WatchdogVerdict.CONTINUE
    resumed_after_second = [
        e for e in _events(captured) if e.kind == WaitingStatusKind.STALL_RESUMED
    ]
    assert len(resumed_after_second) == 1, (
        f"Expected exactly one STALL_RESUMED listener event on the non-stuck tick; "
        f"got {len(resumed_after_second)}: {[e.kind for e in _events(captured)]}"
    )
    assert _stall_state(watchdog) is False

    # Tick 3: classifier still returns LOADING -> no additional events.
    third_now = clock.monotonic() + 1.0
    third_verdict = watchdog._gate_fire(
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        now=third_now,
        idle_elapsed=1.0,
    )
    assert third_verdict == WatchdogVerdict.CONTINUE
    stalled_total = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    resumed_total = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALL_RESUMED]
    assert len(stalled_total) == 1, (
        f"Repeated non-stuck ticks must NOT emit duplicate STALLED; got {len(stalled_total)}"
    )
    assert len(resumed_total) == 1, (
        f"Repeated non-stuck ticks must NOT emit duplicate STALL_RESUMED; got {len(resumed_total)}"
    )


def test_gate_deferral_does_not_clear_stuck_or_fire_stall() -> None:
    """A STUCK verdict still fires AND keeps the stall active on the next deferral.

    DA-001 contract: the deferral path only clears a stall that
    was previously set by ``SILENT_SUBAGENT`` (or any other
    non-``STUCK`` fire path); a ``STUCK`` verdict stays stalled
    because the gate is firing that same tick, not deferring. The
    subsequent tick's classifier may return a different non-stuck
    kind (e.g. ``LOADING``) -- in that case the deferral path
    clears the stall. This test pins the asymmetry: a single STUCK
    tick followed by a non-stuck tick is exactly one STALLED +
    exactly one STALL_RESUMED, and ``is_stalled`` ends False.
    """
    captured: list[WaitingStatusEvent] = []
    watchdog, clock = _make_watchdog(listener=captured.append)

    sequence: list[StuckKind] = [StuckKind.STUCK, StuckKind.LOADING]
    _attr = "_classify_stuck_now"

    def _sequence_now(
        *,
        now: float,
        idle_elapsed: float,
        corroboration: CorroborationSnapshot | None = None,
    ) -> StuckKind:
        if sequence:
            return sequence.pop(0)
        return StuckKind.LOADING

    setattr(watchdog, _attr, _sequence_now)

    first_now = clock.monotonic() + 60.0
    first_verdict = watchdog._gate_fire(
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        now=first_now,
        idle_elapsed=60.0,
    )
    assert first_verdict == WatchdogVerdict.FIRE
    assert _stall_state(watchdog) is True

    second_now = clock.monotonic() + 1.0
    second_verdict = watchdog._gate_fire(
        WatchdogFireReason.NO_OUTPUT_DEADLINE,
        now=second_now,
        idle_elapsed=1.0,
    )
    assert second_verdict == WatchdogVerdict.CONTINUE
    assert _stall_state(watchdog) is False

    stalled_total = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    resumed_total = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALL_RESUMED]
    assert len(stalled_total) == 1
    assert len(resumed_total) == 1


# ---------------------------------------------------------------------------
# wt-047-stall-label (DA-002): the watchdog STALLED / STALL_RESUMED
# transition events carry the correct idle_elapsed_seconds (the
# only operator-truthful elapsed value) so the subscriber renders a
# line with the watchdog actual measurement, not the always-0.0
# current_run_seconds.
# ---------------------------------------------------------------------------


def test_set_stall_emitted_event_carries_idle_elapsed_seconds() -> None:
    """DA-002 regression: STALLED event carries idle_elapsed_seconds=42.0.

    The watchdog already passes idle_elapsed through to the
    emitted event; this test pins the watchdog side so a future
    regression in _emit (e.g. accidentally using
    current_run_seconds) is caught at the source rather than
    only at the subscriber rendering.
    """
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _make_watchdog(listener=captured.append)
    watchdog._set_stall(active=True, now=100.0, idle_elapsed=42.0)
    stalled = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALLED]
    assert len(stalled) == 1
    assert stalled[0].idle_elapsed_seconds == 42.0
    assert stalled[0].current_run_seconds == 0.0


def test_set_stall_resumed_emitted_event_carries_idle_elapsed_seconds() -> None:
    """DA-002 regression: STALL_RESUMED event carries idle_elapsed_seconds."""
    captured: list[WaitingStatusEvent] = []
    watchdog, _clock = _make_watchdog(listener=captured.append)
    watchdog._set_stall(active=True, now=100.0, idle_elapsed=42.0)
    watchdog._set_stall(active=False, now=200.0, idle_elapsed=37.0)
    resumed = [e for e in _events(captured) if e.kind == WaitingStatusKind.STALL_RESUMED]
    assert len(resumed) == 1
    assert resumed[0].idle_elapsed_seconds == 37.0
    assert resumed[0].current_run_seconds == 0.0
