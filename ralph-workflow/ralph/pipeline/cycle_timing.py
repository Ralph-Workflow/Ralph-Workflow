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
    total_elapsed_seconds: float
    development_elapsed_seconds: float | None = None
    current_epoch: float | None = None


RoutingTiming = _RoutingTiming


@dataclass(frozen=True)
class CycleTimeboxDecision:
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
    updates: dict[str, object] = {"cycle_timebox_active": False}
    if redirect_reason is not None:
        updates["cycle_timebox_redirect_reason"] = redirect_reason
        updates["cycle_timebox_redirects"] = state.cycle_timebox_redirects + 1
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
    return (
        f"cycle timebox reached {limit_seconds:.0f}s "
        f"(elapsed {elapsed_seconds:.0f}s); "
        f"redirecting to {target}"
    )


def _started(state: PipelineState, *, current_epoch: float | None) -> PipelineState:
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
    ct = policy.cycle_timebox
    if ct is None or routing_timing is None:
        return CycleTimeboxDecision(state=state, target_phase=target_phase)

    if (
        not state.cycle_timebox_active
        and ct.start_source == state.phase
        and ct.start_entry == target_phase
    ):
        return CycleTimeboxDecision(
            state=_started(state, current_epoch=routing_timing.current_epoch),
            target_phase=target_phase,
            timing_started=True,
        )

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
        return CycleTimeboxDecision(state=state, target_phase=target_phase)

    return CycleTimeboxDecision(state=state, target_phase=target_phase)


def development_timebox_redirect_reason(
    *,
    limit_seconds: float,
    elapsed_seconds: float,
    target: str,
) -> str:
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
    dt = policy.development_timebox
    if dt is None or not state.dev_timebox_active or dt.end_entry != next_phase:
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
    if dt.guarded_entry == state.phase:
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
    ct = policy.cycle_timebox
    if ct is None or not state.cycle_timebox_active:
        return state
    if ct.end_entry != next_phase:
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
    ct = policy.cycle_timebox
    if ct is None or state.cycle_timebox_active:
        return state
    if ct.start_entry != target_phase or ct.start_source not in skipped_phases:
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
    outside: set[str] = set()
    frontier = [policy.entry_phase, ct.start_source, ct.end_entry]
    while frontier:
        phase = frontier.pop()
        if phase in outside or phase not in policy.phases:
            continue
        if phase in (ct.start_entry, ct.guarded_entry):
            continue
        outside.add(phase)
        frontier.extend(_declared_targets(policy.phases[phase]))
    return outside


def _declared_targets(phase_def: PhaseDefinition) -> list[str]:
    transitions = phase_def.transitions
    targets = [
        transitions.on_success,
        transitions.on_failure,
        transitions.on_loopback,
    ]
    targets.extend(route.target for route in phase_def.decisions.values())
    return [target for target in targets if target is not None]


_STATUS_ITEM_KEY = "cycle timebox"
_DEVELOPMENT_STATUS_ITEM_KEY = "development timebox"

_SECONDS_PER_MINUTE = 60.0


def cycle_timebox_status_item(
    state: PipelineState,
    *,
    policy: PipelinePolicy,
) -> dict[str, object] | None:
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
    ct = policy.cycle_timebox
    if ct is None or routing_timing is None:
        return None
    if ct.guarded_entry != target_phase or not state.cycle_timebox_active:
        return None
    elapsed = routing_timing.total_elapsed_seconds
    return (
        now_epoch + max(0.0, ct.warning_threshold_seconds - elapsed),
        now_epoch + max(0.0, ct.duration_seconds - elapsed),
    )
