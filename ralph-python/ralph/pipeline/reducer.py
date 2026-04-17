"""Pure reducer: (state, event, policy) -> (new_state, effects).

No I/O, no side effects, fully deterministic.

This module implements the core state machine for the Ralph pipeline.
Given the current state, an event, and the loaded policy, it computes
the new state and any effects to execute.

The reducer is a PURE FUNCTION — it contains no I/O operations,
no logging, and no mutable state. This makes it fully deterministic
and easy to test.

Routing is driven by the policy: phase transitions come from pipeline.toml,
not hardcoded match arms. When no policy is provided, it falls back to
the legacy hardcoded routing for backward compatibility with tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.config.enums import (
    PHASE_COMPLETE,
    PHASE_DEVELOPMENT,
    PHASE_DEVELOPMENT_ANALYSIS,
    PHASE_FAILED,
    PHASE_REVIEW,
    PipelinePhase,
)
from ralph.pipeline.effects import Effect, ExitFailureEffect, SaveCheckpointEffect
from ralph.pipeline.events import Event, PipelineEvent
from ralph.pipeline.handoffs import (
    resolve_next_phase,
    resolve_phase_drain,
    resolve_post_commit_phase,
)
from ralph.pipeline.state import (
    AgentChainState,
    CommitState,
    PipelineState,
    RunMetrics,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.policy.models import PipelinePolicy

# Maximum number of agent retries before giving up
_MAX_AGENT_RETRIES = 3


def reduce(
    state: PipelineState,
    event: Event,
    pipeline_policy: PipelinePolicy | None = None,
) -> tuple[PipelineState, list[Effect]]:
    """Pure state transition function.

    This is the core of the Ralph pipeline state machine. Given the current
    state, an event, and the pipeline policy, it computes the new state
    and any effects to execute.

    Args:
        state: Current pipeline state.
        event: Event to process.
        pipeline_policy: Optional pipeline policy for resolving transitions.
            If None, uses hardcoded transitions for backward compatibility.

    Returns:
        Tuple of (new_state, effects). Effects are instructions for the
        effect handler to execute.
    """
    handlers: dict[
        PipelineEvent,
        Callable[[PipelineState, PipelinePolicy | None], tuple[PipelineState, list[Effect]]],
    ] = {
        PipelineEvent.AGENT_SUCCESS: _handle_agent_success,
        PipelineEvent.AGENT_FAILURE: _ignore_policy(_handle_agent_failure),
        PipelineEvent.AGENT_RETRY: _ignore_policy(_handle_agent_retry),
        PipelineEvent.ANALYSIS_SUCCESS: _handle_analysis_success,
        PipelineEvent.ANALYSIS_LOOPBACK: _handle_analysis_loopback,
        PipelineEvent.REVIEW_CLEAN: _handle_review_clean,
        PipelineEvent.REVIEW_ISSUES_FOUND: _handle_review_issues_found,
        PipelineEvent.FIX_SUCCESS: _handle_fix_success,
        PipelineEvent.FIX_FAILURE: _handle_fix_failure,
        PipelineEvent.COMMIT_SUCCESS: _handle_commit_success,
        PipelineEvent.COMMIT_FAILURE: _ignore_policy(_handle_commit_failure),
        PipelineEvent.CHECKPOINT_SAVED: _ignore_policy(_handle_checkpoint_saved),
        PipelineEvent.CONTEXT_CLEANED: _return_state,
        PipelineEvent.INTERRUPTED: _ignore_policy(_handle_interrupted),
        PipelineEvent.COMPLETE: _ignore_policy(_handle_complete),
        PipelineEvent.FAILED: _ignore_policy(_handle_failed),
        PipelineEvent.PHASE_ADVANCE: _handle_phase_advance,
    }
    handler = handlers.get(event)
    if handler is None:
        return state, []
    return handler(state, pipeline_policy)


def _ignore_policy(
    handler: Callable[[PipelineState], tuple[PipelineState, list[Effect]]],
) -> Callable[[PipelineState, PipelinePolicy | None], tuple[PipelineState, list[Effect]]]:
    def wrapper(
        state: PipelineState,
        _policy: PipelinePolicy | None,
    ) -> tuple[PipelineState, list[Effect]]:
        return handler(state)

    return wrapper


def _return_state(
    state: PipelineState,
    _policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    return state, []


def _handle_agent_success(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle successful agent completion."""
    if policy is not None:
        return _policy_handle_agent_success(state, policy)
    return _legacy_handle_agent_success(state)


def _policy_handle_agent_success(
    state: PipelineState,
    policy: PipelinePolicy,
) -> tuple[PipelineState, list[Effect]]:
    """Policy-driven agent success handling."""
    phase_def = policy.phases.get(state.phase)
    if phase_def is None:
        return _advance_to_terminal(state, PHASE_FAILED, f"Unknown phase: {state.phase}")

    if phase_def.requires_commit and not state.commit.agent_invoked:
        updated_commit = CommitState(
            message_prepared=state.commit.message_prepared,
            diff_prepared=state.commit.diff_prepared,
            agent_invoked=True,
        )
        return state.copy_with(commit=updated_commit), []

    if phase_def.embeds_analysis:
        return _handle_analysis_success(state, policy)

    try:
        next_phase = resolve_next_phase(state.phase, "success", policy)
        return _advance_phase(state, next_phase, policy)
    except ValueError as exc:
        return _advance_to_terminal(
            state, PHASE_FAILED, f"Routing error after agent success in '{state.phase}': {exc}"
        )


def _legacy_handle_agent_success(
    state: PipelineState,
) -> tuple[PipelineState, list[Effect]]:
    """Legacy hardcoded agent success routing."""
    if state.phase == PHASE_DEVELOPMENT:
        if state.iteration + 1 < state.total_iterations:
            new_state = state.copy_with(iteration=state.iteration + 1)
            return new_state, []

        new_state = state.copy_with(
            phase=PHASE_REVIEW,
            previous_phase=PHASE_DEVELOPMENT,
            reviewer_pass=0,
        )
        return new_state, []

    if state.phase == PHASE_REVIEW:
        new_state = state.copy_with(
            phase=PHASE_COMPLETE,
            previous_phase=PHASE_REVIEW,
        )
        return new_state, []

    if state.phase == "planning":
        new_state = state.copy_with(
            phase=PHASE_DEVELOPMENT,
            previous_phase="planning",
        )
        return new_state, []

    return state, []


def _handle_agent_failure(state: PipelineState) -> tuple[PipelineState, list[Effect]]:
    """Handle agent failure with retry/fallback logic."""
    chain = state.chain_for_phase(state.phase)
    if chain is None:
        new_state = state.copy_with(
            phase=PHASE_FAILED,
            previous_phase=state.phase,
            last_error=f"No tracked agent chain for {state.phase}",
        )
        return new_state, [ExitFailureEffect(reason=f"No tracked agent chain for {state.phase}")]

    if chain.retries < _MAX_AGENT_RETRIES:
        new_chain = AgentChainState(
            agents=chain.agents,
            current_index=chain.current_index,
            retries=chain.retries + 1,
        )
        new_metrics = RunMetrics(
            total_agent_calls=state.metrics.total_agent_calls,
            total_continuations=state.metrics.total_continuations,
            total_fallbacks=state.metrics.total_fallbacks,
            total_retries=state.metrics.total_retries + 1,
        )
        new_state = state.with_phase_chain(state.phase, new_chain).copy_with(metrics=new_metrics)
        return new_state, []

    if chain.current_index + 1 < len(chain.agents):
        new_chain = AgentChainState(
            agents=chain.agents,
            current_index=chain.current_index + 1,
            retries=0,
        )
        new_metrics = RunMetrics(
            total_agent_calls=state.metrics.total_agent_calls,
            total_continuations=state.metrics.total_continuations,
            total_fallbacks=state.metrics.total_fallbacks + 1,
            total_retries=state.metrics.total_retries,
        )
        new_state = state.with_phase_chain(state.phase, new_chain).copy_with(metrics=new_metrics)
        return new_state, []

    new_state = state.copy_with(
        phase=PHASE_FAILED,
        previous_phase=state.phase,
        last_error=f"Agent chain exhausted in {state.phase}",
    )
    return new_state, [ExitFailureEffect(reason="Agent chain exhausted")]


def _handle_agent_retry(state: PipelineState) -> tuple[PipelineState, list[Effect]]:
    """Handle agent retry request."""
    chain = state.chain_for_phase(state.phase)
    if chain is None:
        new_state = state.copy_with(
            phase=PHASE_FAILED,
            previous_phase=state.phase,
            last_error=f"No tracked agent chain for {state.phase}",
        )
        return new_state, [ExitFailureEffect(reason=f"No tracked agent chain for {state.phase}")]

    new_chain = AgentChainState(
        agents=chain.agents,
        current_index=chain.current_index,
        retries=chain.retries + 1,
    )
    new_metrics = RunMetrics(
        total_agent_calls=state.metrics.total_agent_calls,
        total_continuations=state.metrics.total_continuations + 1,
        total_fallbacks=state.metrics.total_fallbacks,
        total_retries=state.metrics.total_retries,
    )

    new_state = state.with_phase_chain(state.phase, new_chain).copy_with(metrics=new_metrics)
    return new_state, []


def _handle_analysis_success(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle successful analysis decision (continue/approve)."""
    if policy is not None:
        try:
            next_phase = resolve_next_phase(state.phase, "success", policy)
            return _advance_phase(state, next_phase, policy)
        except ValueError as exc:
            return _advance_to_terminal(
                state,
                PHASE_FAILED,
                f"Routing error after analysis success in '{state.phase}': {exc}",
            )

    return _legacy_handle_analysis_success(state)


def _legacy_handle_analysis_success(
    state: PipelineState,
) -> tuple[PipelineState, list[Effect]]:
    """Legacy analysis success routing."""
    if state.phase == "development_analysis":
        new_state = state.copy_with(
            phase="development_commit",
            previous_phase=state.phase,
        )
        return new_state, []

    if state.phase == "review_analysis":
        new_state = state.copy_with(
            phase="review_commit",
            previous_phase=state.phase,
        )
        return new_state, []

    return state, []


def _handle_analysis_loopback(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle analysis loopback decision (retry/request changes)."""
    if policy is not None:
        try:
            next_phase = resolve_next_phase(state.phase, "loopback", policy)
            return _advance_phase(state, next_phase, policy)
        except ValueError as exc:
            return _advance_to_terminal(
                state,
                PHASE_FAILED,
                f"Routing error after analysis loopback in '{state.phase}': {exc}",
            )

    return _legacy_handle_analysis_loopback(state)


def _legacy_handle_analysis_loopback(
    state: PipelineState,
) -> tuple[PipelineState, list[Effect]]:
    """Legacy analysis loopback routing."""
    if state.phase == "development_analysis":
        new_state = state.copy_with(
            phase=PHASE_DEVELOPMENT,
            previous_phase=state.phase,
        )
        return new_state, []

    if state.phase == "review_analysis":
        new_state = state.copy_with(
            phase="fix",
            previous_phase=state.phase,
        )
        return new_state, []

    return state, []


def _handle_review_clean(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle clean review (no issues found)."""
    if policy is not None:
        try:
            next_phase = resolve_next_phase(state.phase, "success", policy)
            return _advance_phase(state, next_phase, policy)
        except ValueError as exc:
            return _advance_to_terminal(
                state, PHASE_FAILED, f"Routing error after review clean in '{state.phase}': {exc}"
            )

    new_state = state.copy_with(
        phase="review_commit",
        previous_phase=PHASE_REVIEW,
        review_issues_found=False,
    )
    return new_state, []


def _handle_review_issues_found(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle review with issues found."""
    if policy is not None:
        try:
            next_phase = resolve_next_phase(state.phase, "loopback", policy)
            return _advance_phase(state, next_phase, policy)
        except ValueError as exc:
            return _advance_to_terminal(
                state,
                PHASE_FAILED,
                f"Routing error after review issues found in '{state.phase}': {exc}",
            )

    if state.reviewer_pass + 1 < state.total_reviewer_passes:
        new_state = state.copy_with(
            phase="fix",
            previous_phase=PHASE_REVIEW,
            review_issues_found=True,
            reviewer_pass=state.reviewer_pass + 1,
        )
    else:
        new_state = state.copy_with(
            phase="review_commit",
            previous_phase=PHASE_REVIEW,
            review_issues_found=True,
        )
    return new_state, []


def _handle_fix_success(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle successful fix."""
    if policy is not None:
        try:
            next_phase = resolve_next_phase(state.phase, "success", policy)
            return _advance_phase(state, next_phase, policy)
        except ValueError as exc:
            return _advance_to_terminal(
                state, PHASE_FAILED, f"Routing error after fix success in '{state.phase}': {exc}"
            )

    new_state = state.copy_with(
        phase=PHASE_REVIEW,
        previous_phase="fix",
    )
    return new_state, []


def _handle_fix_failure(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle fix failure."""
    if policy is not None:
        try:
            next_phase = resolve_next_phase(state.phase, "failure", policy)
            if next_phase == PHASE_FAILED:
                new_state = state.copy_with(
                    phase=PHASE_FAILED,
                    previous_phase=state.phase,
                )
                return new_state, [ExitFailureEffect(reason="Fix phase failed")]
            return _advance_phase(state, next_phase, policy)
        except ValueError as exc:
            return _advance_to_terminal(
                state, PHASE_FAILED, f"Routing error after fix failure in '{state.phase}': {exc}"
            )

    if state.reviewer_pass + 1 < state.total_reviewer_passes:
        new_state = state.copy_with(
            phase="fix",
            previous_phase=PHASE_REVIEW,
            reviewer_pass=state.reviewer_pass + 1,
        )
        return new_state, []

    new_state = state.copy_with(
        phase="review_commit",
        previous_phase=PHASE_REVIEW,
    )
    return new_state, []


def _handle_commit_success(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle successful commit."""
    if policy is not None:
        try:
            next_phase = resolve_post_commit_phase(state, policy)
            return _advance_phase(state, next_phase, policy)
        except ValueError as exc:
            return _advance_to_terminal(
                state, PHASE_FAILED, f"Routing error after commit success in '{state.phase}': {exc}"
            )

    new_state = state.copy_with(
        phase=PHASE_COMPLETE,
        previous_phase="review_commit",
    )
    return new_state, []


def _handle_commit_failure(state: PipelineState) -> tuple[PipelineState, list[Effect]]:
    """Handle commit failure."""
    new_state = state.copy_with(
        phase=PHASE_FAILED,
        previous_phase=state.phase,
        last_error="Commit failed",
    )
    return new_state, [ExitFailureEffect(reason="Commit failed")]


def _handle_checkpoint_saved(state: PipelineState) -> tuple[PipelineState, list[Effect]]:
    """Handle checkpoint saved event."""
    new_state = state.copy_with(checkpoint_saved_count=state.checkpoint_saved_count + 1)
    return new_state, []


def _handle_interrupted(state: PipelineState) -> tuple[PipelineState, list[Effect]]:
    """Handle user interruption."""
    new_state = state.copy_with(interrupted_by_user=True)
    return new_state, [SaveCheckpointEffect()]


def _handle_complete(state: PipelineState) -> tuple[PipelineState, list[Effect]]:
    """Handle pipeline completion."""
    new_state = state.copy_with(phase=PHASE_COMPLETE)
    return new_state, []


def _handle_failed(state: PipelineState) -> tuple[PipelineState, list[Effect]]:
    """Handle pipeline failure."""
    new_state = state.copy_with(
        phase=PHASE_FAILED,
        last_error=state.last_error or "Unknown failure",
    )
    return new_state, [ExitFailureEffect(reason=state.last_error or "Unknown failure")]


def _handle_phase_advance(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle explicit phase advance request."""
    if policy is not None:
        try:
            next_phase = resolve_next_phase(state.phase, "success", policy)
            return _advance_phase(state, next_phase, policy)
        except ValueError as exc:
            return _advance_to_terminal(
                state, PHASE_FAILED, f"Routing error after phase advance in '{state.phase}': {exc}"
            )
    return state, []


def _advance_phase(
    state: PipelineState,
    target_phase: PipelinePhase,
    policy: PipelinePolicy | None = None,
) -> tuple[PipelineState, list[Effect]]:
    """Advance to a new phase with proper state resets.

    Args:
        state: Current pipeline state.
        target_phase: Phase to advance to.

    Returns:
        Tuple of (new_state, effects).
    """
    updates: dict[str, object] = {
        "phase": target_phase,
        "previous_phase": state.phase,
    }

    if target_phase in ("development_commit", "review_commit"):
        updates["commit"] = CommitState()

    if target_phase == PHASE_DEVELOPMENT and state.phase != PHASE_DEVELOPMENT_ANALYSIS:
        updates["development_budget_remaining"] = max(0, state.development_budget_remaining - 1)
    elif target_phase == PHASE_REVIEW:
        updates["review_budget_remaining"] = max(0, state.review_budget_remaining - 1)

    if policy is not None:
        updates["current_drain"] = resolve_phase_drain(target_phase, policy)

    new_state = state.copy_with(**updates)
    return new_state, []


def _advance_to_terminal(
    state: PipelineState,
    terminal: PipelinePhase,
    reason: str,
) -> tuple[PipelineState, list[Effect]]:
    """Advance to a terminal state.

    Args:
        state: Current pipeline state.
        terminal: Terminal state (complete or failed).
        reason: Reason for the terminal state.

    Returns:
        Tuple of (new_state, effects).
    """
    updates: dict[str, object] = {
        "phase": terminal,
        "previous_phase": state.phase,
    }
    if terminal == PHASE_FAILED:
        updates["last_error"] = reason

    new_state = state.copy_with(**updates)
    effects: list[Effect] = []
    if terminal == PHASE_FAILED:
        effects.append(ExitFailureEffect(reason=reason))

    return new_state, effects
