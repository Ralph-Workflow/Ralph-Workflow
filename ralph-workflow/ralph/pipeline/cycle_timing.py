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
* The timer ends when routing enters ``end_entry`` (the final-commit path) or
  when an expired guarded entry is redirected to ``finalization_target``.
* The deadline is enforced only at the routing boundary: an invocation already
  in progress is never interrupted by this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PipelinePolicy


@dataclass(frozen=True)
class _RoutingTiming:
    """Sampled monotonic time and computed cycle elapsed seconds for one reduce call.

    ``monotonic_now`` is the runner's sampled clock value; ``total_elapsed_seconds``
    is the cycle timebox's consumed seconds for the current cycle (folded by the
    runner before the call). Pure routing code reads these instead of a clock.
    """

    monotonic_now: float
    total_elapsed_seconds: float


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
    timing_ended: bool = False


def _concluded(
    state: PipelineState,
    *,
    redirect_reason: str | None = None,
) -> PipelineState:
    """Return a copy with the cycle marked concluded.

    ``cycle_timebox_consumed_seconds`` is PRESERVED so the run-time report
    can still show the elapsed/configured duration after finalization; the
    next :func:`_started` resets it to zero for the fresh cycle. When
    ``redirect_reason`` is supplied (a deadline expiry redirect) it is
    recorded on the state so operator surfaces can distinguish a deadline
    redirect from an ordinary completion.
    """
    updates: dict[str, object] = {"cycle_timebox_active": False}
    if redirect_reason is not None:
        updates["cycle_timebox_redirect_reason"] = redirect_reason
    return state.copy_with(**updates)


def _started(state: PipelineState) -> PipelineState:
    """Return a copy with a fresh active cycle (zero consumed seconds)."""
    return state.copy_with(
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=0.0,
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
    whether the timer started, ended, or the route was redirected. When the
    policy declares no timebox, or no timing context was supplied, the decision
    is a no-op pass-through.
    """
    ct = policy.cycle_timebox
    if ct is None or routing_timing is None:
        return CycleTimeboxDecision(state=state, target_phase=target_phase)

    # Entering the final-commit path ends cycle timing. This also covers the
    # finalization_target when it equals end_entry (the bundled workflow) and
    # any normal (non-expired) entry to the final-commit path. Consumed time is
    # preserved (not zeroed) so operator surfaces can report the elapsed
    # duration after the cycle concludes.
    if target_phase in (ct.end_entry, ct.finalization_target) and state.cycle_timebox_active:
        return CycleTimeboxDecision(
            state=_concluded(state),
            target_phase=target_phase,
            timing_ended=True,
        )

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
            state=_started(state),
            target_phase=target_phase,
            timing_started=True,
        )

    # Guard the configured development entry (active timer).
    if target_phase == ct.guarded_entry:
        if routing_timing.total_elapsed_seconds >= ct.duration_seconds:
            reason = (
                f"cycle timebox reached {ct.duration_seconds:.0f}s "
                f"(elapsed {routing_timing.total_elapsed_seconds:.0f}s); "
                f"redirecting to {ct.finalization_target}"
            )
            return CycleTimeboxDecision(
                state=_concluded(state, redirect_reason=reason),
                target_phase=ct.finalization_target,
                redirected=True,
                redirect_reason=reason,
                timing_ended=True,
            )
        # Active and within budget: permit the entry.
        return CycleTimeboxDecision(state=state, target_phase=target_phase)

    return CycleTimeboxDecision(state=state, target_phase=target_phase)


def initialize_legacy_cycle_on_resume(
    state: PipelineState,
    policy: PipelinePolicy,
) -> PipelineState:
    """Initialize cycle timing for an older checkpoint resumed mid-cycle.

    A legacy checkpoint (written before this feature) has no cycle-timing
    state. When such a checkpoint resumes directly inside the development
    loop (at the start or guarded entry), start a fresh active timebox with
    zero consumed seconds so the timer is tracked going forward without
    charging pre-resume downtime. Checkpoints that already carry cycle
    state (active or previously concluded) are left untouched.
    """
    ct = policy.cycle_timebox
    if ct is None:
        return state
    if state.cycle_timebox_active or state.cycle_timebox_consumed_seconds > 0:
        return state
    if state.phase in (ct.start_entry, ct.guarded_entry):
        return _started(state)
    return state


def cycle_timebox_warning(
    state: PipelineState,
    target_phase: str,
    *,
    policy: PipelinePolicy,
    routing_timing: RoutingTiming | None,
) -> dict[str, object] | None:
    """Return the soft warning payload for a guarded entry at/after 80% elapsed.

    The warning is emitted only for the configured guarded entry when the cycle
    is active and elapsed time has reached the derived 80% warning point but the
    deadline has not expired (expired entries are redirected, not warned). The
    payload carries elapsed seconds, remaining seconds, and the deadline
    consequence so the caller can inject it into the agent prompt and the
    operator status surface.
    """
    ct = policy.cycle_timebox
    if ct is None or routing_timing is None:
        return None
    if target_phase != ct.guarded_entry:
        return None
    if not state.cycle_timebox_active:
        return None
    elapsed = routing_timing.total_elapsed_seconds
    if elapsed < ct.warning_threshold_seconds:
        return None
    if elapsed >= ct.duration_seconds:
        # Expired entries are redirected, not warned.
        return None
    remaining = max(0.0, ct.duration_seconds - elapsed)
    return {
        "elapsed_seconds": elapsed,
        "remaining_seconds": remaining,
        "duration_seconds": ct.duration_seconds,
        "finalization_target": ct.finalization_target,
    }
