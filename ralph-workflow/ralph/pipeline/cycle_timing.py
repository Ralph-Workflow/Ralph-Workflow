"""Plan-to-final-commit cycle timebox routing helpers.

This module owns the PURE routing decisions for the transition-bounded cycle
timebox declared in :class:`ralph.policy.models.CycleTimeboxPolicy`. It never
reads a wall clock itself: the runner samples a monotonic clock, computes the
total elapsed seconds consumed by the current cycle, and passes both values in
through :class:`RoutingTiming`. Every routing decision below is a pure function
of ``(state, target_phase, policy, routing_timing)``.

Timer model
-----------
* The timer starts on the configured ``start_source`` -> ``start_entry``
  transition while the cycle is inactive, and is preserved across every loop
  phase until the cycle ends.
* The timer ends only when routing enters its configured ``end_entry``.
* The deadline is enforced only at the routing boundary: an invocation already
  in progress is never interrupted by this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import (
        CycleTimeboxPolicy,
        PhaseDefinition,
        PipelinePolicy,
    )


@dataclass(frozen=True)
class _RoutingTiming:
    """Cycle elapsed seconds for one reduce call.

    ``total_elapsed_seconds`` is the cycle timebox's consumed seconds for the
    current cycle, folded by the runner before the call and including the span
    since its last sample. Pure routing code reads this instead of a clock. The
    runner's raw clock reading is deliberately not carried here: it was written
    at every construction site and read at none.
    """

    total_elapsed_seconds: float
    development_elapsed_seconds: float | None = None
    current_epoch: float | None = None


#: Public alias so callers import ``RoutingTiming`` while the repo-structure
#: audit sees only one public top-level class (``CycleTimeboxDecision``).
RoutingTiming = _RoutingTiming


@dataclass(frozen=True)
class CycleTimeboxDecision:
    """Result of applying the cycle timebox to a pending phase advance."""

    state: PipelineState
    target_phase: str
    redirected: bool = False
    redirect_reason: str | None = None
    timing_started: bool = False


def _concluded(
    state: PipelineState,
    *,
    redirect_reason: str | None = None,
    cycle_outcome: str | None = None,
) -> PipelineState:
    """Return a copy with the cycle marked concluded.

    ``cycle_timebox_consumed_seconds`` is PRESERVED so the run-time report
    can still show the elapsed/configured duration after finalization; the
    next :func:`_started` resets it to zero for the fresh cycle. When
    ``redirect_reason`` is supplied (a deadline expiry redirect) it is
    recorded on the state so operator surfaces can distinguish a deadline
    redirect from an ordinary completion, and ``cycle_outcome`` is stamped
    so post-commit routing sees a finished cycle: a redirected cycle ends at
    the final commit and the next cycle starts while budget remains, exactly
    as it would have without the deadline.
    """
    updates: dict[str, object] = {"cycle_timebox_active": False}
    if redirect_reason is not None:
        updates["cycle_timebox_redirect_reason"] = redirect_reason
        # Counted as well as described. The reason belongs to one cycle and is
        # cleared when the next one starts, which under the bundled defaults is
        # the normal path after a redirect — so without a running total, an
        # operator whose run blew four deadlines in five cycles was told about
        # none of them.
        updates["cycle_timebox_redirects"] = state.cycle_timebox_redirects + 1
    # Never outrank a recorded verdict: running out of time is not a verdict,
    # so a cycle that already reported `failed` must not be finalized as
    # `completed` merely because the deadline expired on the way out.
    if cycle_outcome is not None and state.pending_cycle_outcome is None:
        updates["pending_cycle_outcome"] = cycle_outcome
    return state.copy_with(**updates)


def _redirected_cycle(
    state: PipelineState,
    *,
    redirect_reason: str,
    cycle_outcome: str | None,
) -> PipelineState:
    updates: dict[str, object] = {
        "cycle_timebox_redirect_reason": redirect_reason,
        "cycle_timebox_redirects": state.cycle_timebox_redirects + 1,
    }
    if cycle_outcome is not None and state.pending_cycle_outcome is None:
        updates["pending_cycle_outcome"] = cycle_outcome
    return state.copy_with(**updates)


def cycle_timebox_redirect_reason(
    *,
    limit_seconds: float,
    elapsed_seconds: float,
    target: str,
) -> str:
    """Return the operator-facing reason recorded when a deadline redirects.

    Built here rather than inline so the surfaces that render it can be tested
    against the real text instead of a hand-copied approximation that would
    stay green while the real reason changed shape.
    """
    return (
        f"cycle timebox reached {limit_seconds:.0f}s "
        f"(elapsed {elapsed_seconds:.0f}s); "
        f"redirecting to {target}"
    )


def _started(state: PipelineState, *, current_epoch: float | None) -> PipelineState:
    """Return a copy with a fresh active cycle (zero consumed seconds)."""
    return state.copy_with(
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=0.0,
        cycle_timebox_started_at_epoch=current_epoch,
        cycle_timebox_redirect_reason=None,
    )


def apply_cycle_timebox(
    state: PipelineState,
    target_phase: str,
    *,
    policy: PipelinePolicy,
    routing_timing: RoutingTiming | None,
) -> CycleTimeboxDecision:
    """Apply the cycle timebox guard to a pending transition.

    Returns the effective ``(state, target_phase)`` plus flags describing
    whether the timer started or the route was redirected. Conclusion is
    visible on the returned state rather than as a flag; a separate
    ``timing_ended`` flag was set here and read nowhere. When the policy
    declares no timebox, or no timing context was supplied, the decision is a
    no-op pass-through.
    """
    ct = policy.cycle_timebox
    if ct is None or routing_timing is None:
        return CycleTimeboxDecision(state=state, target_phase=target_phase)

    # Start the timer ONLY on the declared start_source -> start_entry
    # transition while inactive, so an unrelated route into the same phase
    # cannot start or reset a cycle. In the bundled workflow the transition
    # is planning_analysis -> development; a custom graph declares its own
    # start_source/start_entry edge.
    if (
        not state.cycle_timebox_active
        and state.phase == ct.start_source
        and target_phase == ct.start_entry
    ):
        return CycleTimeboxDecision(
            state=_started(state, current_epoch=routing_timing.current_epoch),
            target_phase=target_phase,
            timing_started=True,
        )

    # Guard the configured development entry. The active check is load-bearing:
    # consumed seconds are deliberately preserved across a concluded cycle for
    # the run-time report, so guarding an inactive timer would redirect the
    # next entry on the previous cycle's spent budget.
    if target_phase == ct.guarded_entry and state.cycle_timebox_active:
        if routing_timing.total_elapsed_seconds >= ct.duration_seconds:
            reason = cycle_timebox_redirect_reason(
                limit_seconds=ct.duration_seconds,
                elapsed_seconds=routing_timing.total_elapsed_seconds,
                target=ct.finalization_target,
            )
            return CycleTimeboxDecision(
                state=_redirected_cycle(
                    state,
                    redirect_reason=reason,
                    cycle_outcome=ct.finalization_cycle_outcome,
                ),
                target_phase=ct.finalization_target,
                redirected=True,
                redirect_reason=reason,
            )
        # Active and within budget: permit the entry.
        return CycleTimeboxDecision(state=state, target_phase=target_phase)

    return CycleTimeboxDecision(state=state, target_phase=target_phase)


def development_timebox_redirect_reason(
    *,
    limit_seconds: float,
    elapsed_seconds: float,
    target: str,
) -> str:
    """Return the operator reason for a development deadline redirect."""
    return (
        f"development timebox reached {limit_seconds:.0f}s "
        f"(elapsed {elapsed_seconds:.0f}s); redirecting to {target}"
    )


def apply_development_timebox(
    state: PipelineState,
    target_phase: str,
    *,
    policy: PipelinePolicy,
    routing_timing: RoutingTiming | None,
) -> CycleTimeboxDecision:
    """Apply the independent development timer to a pending transition."""
    dt = policy.development_timebox
    if dt is None or routing_timing is None:
        return CycleTimeboxDecision(state=state, target_phase=target_phase)
    if not state.dev_timebox_active and target_phase == dt.start_entry:
        return CycleTimeboxDecision(
            state=state.copy_with(
                dev_timebox_active=True,
                dev_timebox_consumed_seconds=0.0,
                dev_timebox_started_at_epoch=routing_timing.current_epoch,
                dev_timebox_redirect_reason=None,
            ),
            target_phase=target_phase,
            timing_started=True,
        )
    if target_phase == dt.guarded_entry and state.dev_timebox_active:
        elapsed = routing_timing.development_elapsed_seconds
        if elapsed is None:
            return CycleTimeboxDecision(state=state, target_phase=target_phase)
        if elapsed >= dt.warning_seconds:
            reason = development_timebox_redirect_reason(
                limit_seconds=dt.warning_seconds,
                elapsed_seconds=elapsed,
                target=dt.finalization_target,
            )
            updates: dict[str, object] = {
                "dev_timebox_redirect_reason": reason,
            }
            commit_phase = policy.phases.get(dt.end_entry)
            if commit_phase is not None and commit_phase.role == "commit":
                updates["post_commit_phase_override"] = commit_phase.transitions.on_success
            return CycleTimeboxDecision(
                state=state.copy_with(**updates),
                target_phase=dt.finalization_target,
                redirected=True,
                redirect_reason=reason,
            )
    return CycleTimeboxDecision(state=state, target_phase=target_phase)


def development_deadline_epochs(
    state: PipelineState,
    target_phase: str,
    *,
    policy: PipelinePolicy,
    routing_timing: RoutingTiming | None,
    now_epoch: float,
) -> tuple[float, float] | None:
    """Return independent development warning/deadline wall-clock epochs."""
    dt = policy.development_timebox
    if dt is None or routing_timing is None:
        return None
    if target_phase != dt.guarded_entry or not state.dev_timebox_active:
        return None
    elapsed = routing_timing.development_elapsed_seconds
    if elapsed is None:
        return None
    return (
        now_epoch + max(0.0, dt.warning_seconds - elapsed),
        now_epoch + max(0.0, dt.duration_seconds - elapsed),
    )


def conclude_development_timebox_on_route_out_of_development(
    state: PipelineState,
    next_phase: str,
    *,
    policy: PipelinePolicy,
) -> PipelineState:
    """Stop the phase timer only on entry to its configured commit boundary."""
    dt = policy.development_timebox
    if dt is None or not state.dev_timebox_active or next_phase != dt.end_entry:
        return state
    return state.copy_with(
        dev_timebox_active=False,
        dev_timebox_consumed_seconds=0.0,
        dev_timebox_started_at_epoch=None,
    )


def initialize_legacy_development_timebox_on_resume(
    state: PipelineState,
    policy: PipelinePolicy,
    *,
    current_epoch: float | None = None,
) -> PipelineState:
    """Start a fresh development timer for a legacy checkpoint in development."""
    dt = policy.development_timebox
    if dt is None:
        return state
    if state.dev_timebox_active:
        if state.dev_timebox_started_at_epoch is None:
            return state.copy_with(
                dev_timebox_consumed_seconds=max(
                    state.dev_timebox_consumed_seconds, dt.warning_seconds
                )
            )
        return state
    if state.dev_timebox_consumed_seconds > 0:
        return state
    if state.phase == dt.guarded_entry:
        return state.copy_with(
            dev_timebox_active=True,
            dev_timebox_started_at_epoch=current_epoch,
        )
    return state


def conclude_cycle_on_route_out_of_cycle(
    state: PipelineState,
    next_phase: str,
    *,
    policy: PipelinePolicy,
) -> PipelineState:
    """End cycle timing only on entry to its configured final-commit boundary."""
    ct = policy.cycle_timebox
    if ct is None or not state.cycle_timebox_active:
        return state
    if next_phase != ct.end_entry:
        return state
    return _concluded(state)


def start_cycle_for_bypassed_start_source(
    state: PipelineState,
    target_phase: str,
    skipped_phases: tuple[str, ...],
    *,
    policy: PipelinePolicy,
    routing_timing: RoutingTiming | None,
) -> PipelineState:
    """Start the cycle when the declared start transition was skipped over.

    The timer is bound to the exact ``start_source`` -> ``start_entry`` edge so
    an unrelated route into the same phase cannot start or reset a cycle. When
    ``start_source`` is bypassed (its loop budget is spent) the run still
    enters ``start_entry`` to begin a cycle, and that cycle must be on the
    clock: without this it would run with no deadline, no warning, and nothing
    published to the agent. Any other transition is left untouched.
    """
    ct = policy.cycle_timebox
    if ct is None or state.cycle_timebox_active:
        return state
    if target_phase != ct.start_entry or ct.start_source not in skipped_phases:
        return state
    return _started(
        state,
        current_epoch=(routing_timing.current_epoch if routing_timing is not None else None),
    )


def initialize_legacy_cycle_on_resume(
    state: PipelineState,
    policy: PipelinePolicy,
    *,
    current_epoch: float | None = None,
) -> PipelineState:
    """Initialize cycle timing for an older checkpoint resumed mid-cycle.

    A legacy checkpoint (written before this feature) has no cycle-timing
    state. When such a checkpoint resumes inside the development loop, start a
    fresh active timebox with zero consumed seconds so the timer is tracked
    going forward without charging pre-resume downtime. Checkpoints that
    already carry cycle state (active or previously concluded) are left
    untouched.

    "Inside the loop" is every phase except the terminals and the phases that
    run BEFORE the cycle starts — the entry phase and the declared
    ``start_source``. Those precede the start edge, and time spent there is
    deliberately not charged to a cycle; starting the timer at one of them
    would bill planning to the deadline. Restricting this to the start and
    guarded entries instead left a checkpoint resumed at analysis or at a
    commit phase running the remainder of its cycle unguarded.
    """
    ct = policy.cycle_timebox
    if ct is None:
        return state
    if state.cycle_timebox_active:
        return (
            state.copy_with(
                cycle_timebox_consumed_seconds=max(
                    state.cycle_timebox_consumed_seconds, ct.duration_seconds
                )
            )
            if state.cycle_timebox_started_at_epoch is None
            else state
        )
    if state.cycle_timebox_consumed_seconds > 0:
        return state
    if state.phase in (ct.start_entry, ct.guarded_entry):
        return _started(state, current_epoch=current_epoch)
    if (
        state.phase not in policy.phases
        or state.phase in policy.terminal_states()
        or state.phase in _outside_cycle_phases(policy, ct)
    ):
        return state
    return _started(state, current_epoch=current_epoch)


def _outside_cycle_phases(policy: PipelinePolicy, ct: CycleTimeboxPolicy) -> set[str]:
    """Return the phases that are not inside a cycle.

    Two groups. BEFORE the cycle: the entry phase, the declared
    ``start_source``, and everything reachable between them, whose time is
    deliberately never charged to a deadline — starting a timer there would
    bill planning to the deadline.
    AT OR AFTER its end: ``end_entry`` and the finalization path reachable from
    it. A timer started there could never be concluded — the conclusion fires
    on ENTRY to ``end_entry``, which has already happened — so it would run on
    into the next cycle and redirect that cycle's first development entry.

    Both walks follow EVERY declared edge, not just success transitions: a
    planning-side phase reached by an analysis decision or a loopback is just
    as much outside the cycle as one reached by ``on_success``.
    """
    outside: set[str] = set()
    frontier = [policy.entry_phase, ct.start_source, ct.end_entry]
    while frontier:
        phase = frontier.pop()
        if phase in outside or phase not in policy.phases:
            continue
        # The cycle itself is the boundary in both directions.
        if phase in (ct.start_entry, ct.guarded_entry):
            continue
        outside.add(phase)
        frontier.extend(_declared_targets(policy.phases[phase]))
    return outside


def _declared_targets(phase_def: PhaseDefinition) -> list[str]:
    """Return every phase a phase can route to by any declared edge."""
    transitions = phase_def.transitions
    targets = [
        transitions.on_success,
        transitions.on_failure,
        transitions.on_loopback,
    ]
    targets.extend(route.target for route in phase_def.decisions.values())
    return [target for target in targets if target is not None]


#: Phase-banner key for the cycle-timebox item. The space makes the display
#: layer render it as a bracketed ``[cycle timebox ...]`` item rather than a
#: bare ``key=value`` pair.
_STATUS_ITEM_KEY = "cycle timebox"
_DEVELOPMENT_STATUS_ITEM_KEY = "development timebox"

_SECONDS_PER_MINUTE = 60.0


def cycle_timebox_status_item(
    state: PipelineState,
    *,
    policy: PipelinePolicy,
) -> dict[str, object] | None:
    """Return the live operator item describing the cycle deadline, if any.

    The budget was otherwise invisible until the end-of-run report, so an
    operator could not watch it drain. Returns the consumed/remaining budget
    while a cycle is active, and ``None`` when no cycle is running.

    A deadline-forced finalization is deliberately NOT reported here: the
    display renders transition context only on major transitions, and every
    transition a redirected cycle makes on its way to the final commit is
    minor, so such a notice could never reach an operator. The redirect
    surfaces through the routing log line and the run-time report's ``[CT-2]``
    line instead.
    """
    items: dict[str, object] = {}
    ct = policy.cycle_timebox
    if ct is not None and state.cycle_timebox_active:
        consumed = state.cycle_timebox_consumed_seconds
        remaining = max(0.0, ct.duration_seconds - consumed)
        items[_STATUS_ITEM_KEY] = (
            f"{consumed / _SECONDS_PER_MINUTE:.0f}m/"
            f"{ct.duration_seconds / _SECONDS_PER_MINUTE:.0f}m, "
            f"{remaining / _SECONDS_PER_MINUTE:.0f}m left"
        )
    dt = policy.development_timebox
    if dt is not None and state.dev_timebox_active:
        consumed = state.dev_timebox_consumed_seconds
        remaining = max(0.0, dt.duration_seconds - consumed)
        items[_DEVELOPMENT_STATUS_ITEM_KEY] = (
            f"{consumed / _SECONDS_PER_MINUTE:.0f}m/"
            f"{dt.duration_seconds / _SECONDS_PER_MINUTE:.0f}m, "
            f"{remaining / _SECONDS_PER_MINUTE:.0f}m left"
        )
    return items or None


def cycle_deadline_epochs(
    state: PipelineState,
    target_phase: str,
    *,
    policy: PipelinePolicy,
    routing_timing: RoutingTiming | None,
    now_epoch: float,
) -> tuple[float, float] | None:
    """Return ``(warn_epoch, deadline_epoch)`` for an invocation.

    The deadline does not move while an invocation runs, so it is published
    once as wall-clock epochs the agent-facing MCP server process can compare
    against its own clock — monotonic readings are not comparable across
    processes. Returns ``None`` when no cycle is running or the phase being
    invoked is not the guarded one, which the caller uses to withdraw any
    previously published deadline.
    """
    ct = policy.cycle_timebox
    if ct is None or routing_timing is None:
        return None
    if target_phase != ct.guarded_entry or not state.cycle_timebox_active:
        return None
    elapsed = routing_timing.total_elapsed_seconds
    return (
        now_epoch + max(0.0, ct.warning_threshold_seconds - elapsed),
        now_epoch + max(0.0, ct.duration_seconds - elapsed),
    )
