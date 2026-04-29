"""Pure helpers for phase handoff and drain resolution.

This module centralizes the policy-driven routing and phase-to-drain lookup
used across the reducer and runtime. Keeping these helpers pure makes the
handoff contract easy to unit test and keeps runtime injection limited to the
policy data loaded at the composition root.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.config.enums import PipelinePhase
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PipelinePolicy


def resolve_phase_drain(
    phase: PipelinePhase,
    pipeline_policy: PipelinePolicy,
) -> str | None:
    """Return the configured drain for a phase."""
    phase_def = pipeline_policy.phases.get(phase)
    return phase_def.drain if phase_def is not None else None


def resolve_next_phase(
    current_phase: PipelinePhase,
    signal: str,
    pipeline_policy: PipelinePolicy,
) -> PipelinePhase:
    """Resolve the next phase based on a signal and the pipeline policy."""
    phase_def = pipeline_policy.phases.get(current_phase)
    if phase_def is None:
        msg = f"Cannot resolve transition: phase '{current_phase}' not found"
        raise ValueError(msg)

    transitions = phase_def.transitions

    target: str | None
    if signal == "success":
        target = transitions.on_success
    elif signal == "failure":
        target = transitions.on_failure
    elif signal == "loopback":
        target = transitions.on_loopback
    else:
        msg = f"Unknown signal: {signal}"
        raise ValueError(msg)

    if target is None:
        msg = (
            f"No '{signal}' transition defined for phase '{current_phase}'. "
            f"Define on_{signal} in pipeline.toml or set the phase to terminal."
        )
        raise ValueError(msg)

    if target in ("failed", "complete"):
        return target

    if target not in pipeline_policy.phases:
        msg = (
            f"Transition from '{current_phase}' on signal '{signal}' "
            f"references unknown phase '{target}'"
        )
        raise ValueError(msg)

    return target


def resolve_post_commit_phase(
    state: PipelineState,
    pipeline_policy: PipelinePolicy,
) -> PipelinePhase:
    """Resolve next phase for a successful commit with optional budget guards.

    Routing is driven by post_commit_routes in policy, matched by phase name
    and budget_state. This works for any commit-role phase, not just the
    canonical development_commit/review_commit names.
    """
    phase_def = pipeline_policy.phases.get(state.phase)
    is_commit_phase = phase_def is not None and phase_def.role == "commit"

    if is_commit_phase:
        budget_state = _compute_budget_state(state, pipeline_policy)
        if budget_state is not None:
            for route in pipeline_policy.post_commit_routes:
                if route.when.phase == state.phase and route.when.budget_state == budget_state:
                    return route.target

    return resolve_next_phase(state.phase, "success", pipeline_policy)


def _compute_budget_state(state: PipelineState, pipeline_policy: PipelinePolicy) -> str | None:
    """Determine the budget_state label for the current commit phase.

    Uses only policy-declared counter names from pipeline.budget_counters.
    Counter identity is fully generic: any counter name declared with
    tracks_budget=True is eligible.

    Returns:
        'remaining'  — the phase's own budget counter still has budget left
        'exhausted'  — this counter is at 0 but another tracked counter has budget
        'no_review'  — this counter is at 0 and no other tracked counter has budget
        None         — no tracked budget counter governs this phase
    """
    phase_def = pipeline_policy.phases.get(state.phase)
    if phase_def is None or phase_def.commit_policy is None:
        return None

    counter = phase_def.commit_policy.increments_counter
    if not counter or counter == "none":
        return None

    tracked_cfg = pipeline_policy.budget_counters.get(counter)
    if tracked_cfg is None or not tracked_cfg.tracks_budget:
        return None

    if state.get_budget_remaining(counter) > 0:
        return "remaining"

    for other_name, other_cfg in pipeline_policy.budget_counters.items():
        if (
            other_name != counter
            and other_cfg.tracks_budget
            and state.get_budget_remaining(other_name) > 0
        ):
            return "exhausted"

    return "no_review"
