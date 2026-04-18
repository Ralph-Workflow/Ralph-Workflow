"""Pure helpers for phase handoff and drain resolution.

This module centralizes the policy-driven routing and phase-to-drain lookup
used across the reducer and runtime. Keeping these helpers pure makes the
handoff contract easy to unit test and keeps runtime injection limited to the
policy data loaded at the composition root.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.config.enums import PHASE_COMPLETE

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
    """Resolve next phase for a successful commit with optional budget guards."""
    if state.phase == "development_commit":
        if state.development_budget_remaining > 0:
            budget_state = "remaining"
        elif state.review_budget_remaining == 0:
            return PHASE_COMPLETE
        else:
            budget_state = "exhausted"
    elif state.phase == "review_commit":
        budget_state = "remaining" if state.review_budget_remaining > 0 else "exhausted"
    else:
        return resolve_next_phase(state.phase, "success", pipeline_policy)

    for route in pipeline_policy.post_commit_routes:
        if route.when.phase == state.phase and route.when.budget_state == budget_state:
            return route.target

    return resolve_next_phase(state.phase, "success", pipeline_policy)
