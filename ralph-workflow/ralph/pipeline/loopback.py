"""Shared loopback handlers for policy-driven phases."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline import progress
from ralph.pipeline.handoffs import resolve_next_phase
from ralph.policy.models import PhaseLoopPolicy

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.pipeline.effects import Effect
    from ralph.pipeline.state import PipelineState
    from ralph.policy.models import PhaseDefinition, PipelinePolicy

    _EffectResult = tuple[PipelineState, list[Effect]]


def handle_capped_phase_loopback_policy_driven(
    state: PipelineState,
    policy: PipelinePolicy,
    phase_def: PhaseDefinition,
    *,
    review_outcome: str | None,
    advance_to_failed: Callable[
        [PipelineState, str, PipelinePolicy | None],
        _EffectResult,
    ],
    resolve_or_terminal: Callable[
        [PipelineState, str, PipelinePolicy, str],
        _EffectResult,
    ],
    advance_phase: Callable[
        [PipelineState, str, PipelinePolicy | None],
        _EffectResult,
    ],
) -> _EffectResult:
    """Handle capped loopback using policy-declared loop_policy."""
    loop_policy = phase_def.loop_policy
    if not isinstance(loop_policy, PhaseLoopPolicy):
        return resolve_or_terminal(state, "loopback", policy, "phase loopback")

    iteration_field: str = loop_policy.iteration_state_field
    max_iterations = progress.resolve_analysis_cap(
        state,
        iteration_field,
        policy,
    )
    progress_state = progress.apply_analysis_loopback(
        state,
        state,
        iteration_field,
        max_iterations=max_iterations,
        review_outcome=review_outcome,
    )

    try:
        loopback_target = resolve_next_phase(state.phase, "loopback", policy)
    except ValueError as exc:
        return advance_to_failed(
            progress_state,
            f"Routing error for phase loopback in '{state.phase}': {exc}",
            policy,
        )

    new_state, effects = advance_phase(progress_state, loopback_target, policy)
    return new_state, effects
