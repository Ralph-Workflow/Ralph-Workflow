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
    from ralph.policy.models import CycleTimeboxPolicy, PhaseDefinition, PipelinePolicy


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
                state=_concluded(
                    state,
                    redirect_reason=reason,
                    cycle_outcome=ct.finalization_cycle_outcome,
                ),
                target_phase=ct.finalization_target,
                redirected=True,
                redirect_reason=reason,
                timing_ended=True,
            )
        # Active and within budget: permit the entry.
        return CycleTimeboxDecision(state=state, target_phase=target_phase)

    return CycleTimeboxDecision(state=state, target_phase=target_phase)



def conclude_cycle_on_route_out_of_cycle(
    state: PipelineState,
    next_phase: str,
    *,
    policy: PipelinePolicy,
) -> PipelineState:
    """End cycle timing when routing leaves the cycle by any route.

    Entering ``end_entry`` is the ordinary end, but it is not the only way a
    run reaches its next cycle: a ``result_status_post_commit`` override or an
    agent-chain ``workflow_fallback`` can route straight past the finalization
    path and back to planning. The timer would then never stop — and could
    never restart, since a start requires an inactive cycle — so the next
    cycle would run on the previous one's clock and be redirected before doing
    any development.

    Landing on any phase outside the cycle means this cycle is over,
    whichever route got us there. "Outside" is the same set the legacy-resume
    initializer uses, so a graph with several phases before the cycle is
    handled — not just the entry phase and the declared ``start_source``.
    """
    ct = policy.cycle_timebox
    if ct is None or not state.cycle_timebox_active:
        return state
    # A terminal ends the run, so it ends the cycle too — otherwise the
    # operator surfaces, which read the timer off the final state, report a
    # live budget for a run that is already over.
    if next_phase not in _outside_cycle_phases(policy, ct) | policy.terminal_states():
        return state
    return _concluded(state)


def start_cycle_for_bypassed_start_source(
    state: PipelineState,
    target_phase: str,
    skipped_phases: tuple[str, ...],
    *,
    policy: PipelinePolicy,
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
    return _started(state)


def initialize_legacy_cycle_on_resume(
    state: PipelineState,
    policy: PipelinePolicy,
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
    if state.cycle_timebox_active or state.cycle_timebox_consumed_seconds > 0:
        return state
    if state.phase in (ct.start_entry, ct.guarded_entry):
        return _started(state)
    if state.phase not in policy.phases or state.phase in policy.terminal_states():
        return state
    if state.phase in _outside_cycle_phases(policy, ct):
        return state
    return _started(state)


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
    ct = policy.cycle_timebox
    if ct is None:
        return None
    if not state.cycle_timebox_active:
        return None
    consumed = state.cycle_timebox_consumed_seconds
    remaining = max(0.0, ct.duration_seconds - consumed)
    return {
        _STATUS_ITEM_KEY: (
            f"{consumed / _SECONDS_PER_MINUTE:.0f}m/"
            f"{ct.duration_seconds / _SECONDS_PER_MINUTE:.0f}m, "
            f"{remaining / _SECONDS_PER_MINUTE:.0f}m left"
        )
    }


def cycle_deadline_epochs(
    state: PipelineState,
    target_phase: str,
    *,
    policy: PipelinePolicy,
    routing_timing: RoutingTiming | None,
    now_epoch: float,
) -> tuple[float, float, str] | None:
    """Return ``(warn_epoch, deadline_epoch, finalization_target)`` for an invocation.

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
        ct.finalization_target,
    )


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
