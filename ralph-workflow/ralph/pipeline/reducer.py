"""Pure reducer: (state, event, policy) -> (new_state, effects).

No I/O, no side effects, fully deterministic.

This module implements the core state machine for the Ralph pipeline.
Given the current state, an event, and the loaded policy, it computes
the new state and any effects to execute.

The reducer is a PURE FUNCTION — it contains no I/O operations,
no logging, and no mutable state. This makes it fully deterministic
and easy to test.

Routing is driven by the policy: phase transitions come from pipeline.toml,
not hardcoded match arms.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from ralph.config.enums import (
    PHASE_COMPLETE,
    PHASE_DEVELOPMENT,
    PHASE_FAILED,
    PHASE_MERGE_INTEGRATION,
    PHASE_REVIEW,
    PipelinePhase,
)
from ralph.pipeline import progress
from ralph.pipeline.effects import Effect, SaveCheckpointEffect
from ralph.pipeline.events import (
    Event,
    PhaseFailureEvent,
    PipelineEvent,
    WorkerCompletedEvent,
    WorkerFailedEvent,
    WorkersMergeConflictEvent,
    WorkerStartedEvent,
)
from ralph.pipeline.handoffs import resolve_next_phase, resolve_post_commit_phase
from ralph.pipeline.state import AgentChainState, CommitState, PipelineState, RunMetrics
from ralph.pipeline.worker_state import WorkerState, WorkerStatus

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.policy.models import PipelinePolicy
    from ralph.recovery.controller import RecoveryController

# Maximum number of agent retries before giving up
_MAX_AGENT_RETRIES = 3

# Forbidden sentinel strings that indicate a bug in error handling.
_FORBIDDEN_ERROR_SENTINELS: frozenset[str] = frozenset(
    {
        "Unknown failure",
        "unknown failure",
        "",
        "None",
        "null",
    }
)


def _is_valid_error_message(msg: str | None) -> bool:
    """Check if an error message is descriptive (not a forbidden sentinel)."""
    if msg is None:
        return False
    stripped = msg.strip()
    return stripped != "" and msg not in _FORBIDDEN_ERROR_SENTINELS


def _failure_reason(state: PipelineState, fallback: str) -> str:
    """Extract a descriptive failure reason from state, or use the fallback.

    Uses explicit truthiness check (not `or`) to handle empty-string last_error.
    Empty strings are falsy but not None, so `state.last_error or fallback`
    would work correctly in Python, but explicit checks make the intent clearer
    and guard against future misuse of empty-string assignment.
    """
    if state.last_error:
        return state.last_error
    return fallback


def _restore_work_units(
    state: PipelineState,
    new_state: PipelineState,
) -> PipelineState:
    """Restore work_units if they were lost during state transition."""
    if state.work_units and not new_state.work_units:
        return new_state.copy_with(work_units=state.work_units)
    return new_state


def _dispatch_worker_event(
    state: PipelineState, event: Event
) -> tuple[PipelineState, list[Effect]] | None:
    """Handle worker events, returning None if the event is not a worker event."""
    if isinstance(event, WorkerStartedEvent):
        new_state, effects = _handle_worker_started(state, event)
        return _restore_work_units(state, new_state), effects
    if isinstance(event, WorkerCompletedEvent):
        new_state, effects = _handle_worker_completed(state, event)
        return _restore_work_units(state, new_state), effects
    if isinstance(event, WorkerFailedEvent):
        new_state, effects = _handle_worker_failed(state, event)
        return _restore_work_units(state, new_state), effects
    if isinstance(event, WorkersMergeConflictEvent):
        new_state, effects = _handle_workers_merge_conflict(state, event)
        return _restore_work_units(state, new_state), effects
    return None


def reduce(
    state: PipelineState,
    event: Event,
    pipeline_policy: PipelinePolicy | None = None,
    recovery: RecoveryController | None = None,
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
        recovery: Optional RecoveryController. When supplied, PhaseFailureEvents
            are delegated to it for classification-aware recovery. When None,
            the legacy retry/fallback logic in _handle_agent_failure is used.

    Returns:
        Tuple of (new_state, effects). Effects are instructions for the
        effect handler to execute.
    """
    # Handle PhaseFailureEvent before the generic PipelineEvent dispatch.
    # When a RecoveryController is supplied, delegate to it for classification-aware
    # recovery (intelligent attribution, budget management). When None, use legacy logic.
    if isinstance(event, PhaseFailureEvent):
        if recovery is not None:
            new_state, effects, _ = recovery.handle(
                state,
                event.reason or f"(no reason reported for phase={event.phase})",
                phase=event.phase,
                agent=state.current_agent(),
            )
            return _restore_work_units(state, new_state), effects
        return _handle_phase_failure(state, event)

    # Handle worker events with a unified approach
    worker_result = _dispatch_worker_event(state, event)
    if worker_result is not None:
        return worker_result

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
        PipelineEvent.COMMIT_SKIPPED: _handle_commit_skipped,
        PipelineEvent.COMMIT_FAILURE: _ignore_policy(_handle_commit_failure),
        PipelineEvent.CHECKPOINT_SAVED: _ignore_policy(_handle_checkpoint_saved),
        PipelineEvent.CONTEXT_CLEANED: _return_state,
        PipelineEvent.INTERRUPTED: _ignore_policy(_handle_interrupted),
        PipelineEvent.COMPLETE: _ignore_policy(_handle_complete),
        PipelineEvent.FAILED: _ignore_policy(_handle_failed),
        PipelineEvent.PHASE_ADVANCE: _handle_phase_advance,
        PipelineEvent.FAN_OUT_STARTED: _ignore_policy(_handle_fan_out_started),
        PipelineEvent.WORKERS_RESUMED: _ignore_policy(_handle_workers_resumed),
        PipelineEvent.ALL_WORKERS_COMPLETE: _ignore_policy(_handle_all_workers_complete),
    }
    # At this point, event is a PipelineEvent (PhaseFailureEvent and worker events handled above)
    handler = handlers.get(cast("PipelineEvent", event))
    if handler is None:
        return state, []
    new_state, effects = handler(state, pipeline_policy)
    return _restore_work_units(state, new_state), effects


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


def _enter_failed_recovery(
    state: PipelineState,
    reason: str,
) -> tuple[PipelineState, list[Effect]]:
    new_state = state.copy_with(
        phase=PHASE_FAILED,
        previous_phase=state.phase,
        last_error=reason,
        recovery_epoch=state.recovery_epoch + 1,
    )
    return new_state, []


def _handle_phase_failure(
    state: PipelineState, event: PhaseFailureEvent
) -> tuple[PipelineState, list[Effect]]:
    """Handle PhaseFailureEvent from phase handlers.

    PhaseFailureEvent carries a recoverable flag:
    - recoverable=True: route through _handle_agent_failure retry/fallback logic
    - recoverable=False: route directly to PHASE_FAILED (terminal agent decision)

    In both cases, last_error is set to a descriptive string combining the
    phase name and the reason.
    """
    # Use the event reason if it's descriptive, otherwise synthesize one.
    if event.reason and event.reason.strip():
        failure_message = f"{event.phase}: {event.reason}"
    else:
        failure_message = f"(no reason reported for phase={event.phase})"

    if event.recoverable:
        # Inject the failure message into state.last_error so that
        # _handle_agent_failure preserves it when it transitions to PHASE_FAILED.
        state_with_error = state.copy_with(last_error=failure_message)
        return _handle_agent_failure(state_with_error)
    # Non-recoverable failures now enter centralized recovery instead of
    # terminating the process.
    return _enter_failed_recovery(state, failure_message)


def _handle_agent_success(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle successful agent completion."""
    if policy is None:
        return _advance_to_terminal(
            state, PHASE_FAILED, "No policy loaded for agent success routing"
        )
    return _policy_handle_agent_success(state, policy)


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


def _handle_agent_failure(state: PipelineState) -> tuple[PipelineState, list[Effect]]:
    """Handle agent failure with retry/fallback logic."""
    chain = state.chain_for_phase(state.phase)
    if chain is None:
        failure_reason = _failure_reason(state, f"No tracked agent chain for {state.phase}")
        return _enter_failed_recovery(state, failure_reason)

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

    # Chain exhausted: preserve any informative last_error from PhaseFailureEvent,
    # otherwise construct a descriptive message.
    failure_reason = _failure_reason(
        state,
        (
            f"Agent chain exhausted in phase='{state.phase}' after "
            f"{chain.retries} retries across {len(chain.agents)} agents"
        ),
    )
    return _enter_failed_recovery(state, failure_reason)


def _handle_agent_retry(state: PipelineState) -> tuple[PipelineState, list[Effect]]:
    """Handle agent retry request."""
    chain = state.chain_for_phase(state.phase)
    if chain is None:
        failure_reason = _failure_reason(state, f"No tracked agent chain for {state.phase}")
        return _enter_failed_recovery(state, failure_reason)

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
            new_state, effects = _advance_phase(state, next_phase, policy)
            return progress.apply_analysis_success(state, new_state), effects
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
        new_state, effects = _advance_phase(state, "development_commit")
        return progress.apply_analysis_success(state, new_state), effects

    if state.phase == "review_analysis":
        new_state, effects = _advance_phase(state, "review_commit")
        return progress.apply_analysis_success(state, new_state), effects

    return state, []


def _handle_analysis_loopback(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle analysis loopback decision (retry/request changes)."""
    if policy is None:
        return _legacy_handle_analysis_loopback(state)

    # Policy-driven routing with analysis iteration cap
    if state.phase == "development_analysis":
        return _handle_dev_analysis_loopback(state, policy)
    if state.phase == "review_analysis":
        return _handle_review_analysis_loopback(state, policy)

    # Unknown analysis phase - use general loopback routing with error handling
    try:
        next_phase = resolve_next_phase(state.phase, "loopback", policy)
        return _advance_phase(state, next_phase, policy)
    except ValueError as exc:
        return _advance_to_terminal(
            state,
            PHASE_FAILED,
            f"Routing error after analysis loopback in '{state.phase}': {exc}",
        )


def _handle_dev_analysis_loopback(
    state: PipelineState,
    policy: PipelinePolicy,
) -> tuple[PipelineState, list[Effect]]:
    """Handle development analysis loopback with iteration cap."""
    candidate = state.development_analysis_iteration + 1
    if candidate >= state.max_development_analysis_iterations:
        # Cap hit: force advance to development_commit via success route
        try:
            next_phase = resolve_next_phase(state.phase, "success", policy)
        except ValueError as exc:
            return _advance_to_terminal(
                state,
                PHASE_FAILED,
                f"Routing error after analysis loopback in '{state.phase}': {exc}",
            )
        new_state, effects = _advance_phase(state, next_phase, policy)
        return progress.apply_development_analysis_loopback(state, new_state), effects

    # Normal loopback: route to development and increment counter
    try:
        next_phase = resolve_next_phase(state.phase, "loopback", policy)
    except ValueError as exc:
        return _advance_to_terminal(
            state,
            PHASE_FAILED,
            f"Routing error after analysis loopback in '{state.phase}': {exc}",
        )
    new_state, effects = _advance_phase(state, next_phase, policy)
    return progress.apply_development_analysis_loopback(state, new_state), effects


def _handle_review_analysis_loopback(
    state: PipelineState,
    policy: PipelinePolicy,
) -> tuple[PipelineState, list[Effect]]:
    """Handle review analysis loopback with iteration cap."""
    candidate = state.review_analysis_iteration + 1
    if candidate >= state.max_review_analysis_iterations:
        # Cap hit: force advance to review_commit via success route
        try:
            next_phase = resolve_next_phase(state.phase, "success", policy)
        except ValueError as exc:
            return _advance_to_terminal(
                state,
                PHASE_FAILED,
                f"Routing error after analysis loopback in '{state.phase}': {exc}",
            )
        new_state, effects = _advance_phase(state, next_phase, policy)
        return progress.apply_review_analysis_loopback(state, new_state), effects

    # Normal loopback: route to fix and update bookkeeping
    try:
        next_phase = resolve_next_phase(state.phase, "loopback", policy)
    except ValueError as exc:
        return _advance_to_terminal(
            state,
            PHASE_FAILED,
            f"Routing error after analysis loopback in '{state.phase}': {exc}",
        )
    new_state, effects = _advance_phase(state, next_phase, policy)
    return progress.apply_review_analysis_loopback(state, new_state), effects


def _legacy_handle_analysis_loopback(
    state: PipelineState,
) -> tuple[PipelineState, list[Effect]]:
    """Legacy analysis loopback routing."""
    if state.phase == "development_analysis":
        if state.development_analysis_iteration + 1 >= state.max_development_analysis_iterations:
            new_state, effects = _advance_phase(state, "development_commit")
            return progress.apply_development_analysis_loopback(state, new_state), effects
        new_state, effects = _advance_phase(state, PHASE_DEVELOPMENT)
        return progress.apply_development_analysis_loopback(state, new_state), effects

    if state.phase == "review_analysis":
        if state.review_analysis_iteration + 1 >= state.max_review_analysis_iterations:
            new_state, effects = _advance_phase(state, "review_commit")
            return progress.apply_review_analysis_loopback(state, new_state), effects
        new_state, effects = _advance_phase(state, "fix")
        return progress.apply_review_analysis_loopback(state, new_state), effects

    return state, []


def _handle_review_clean(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle clean review (no issues found).

    When a review is clean (no issues found), the review phase is skipped and
    we advance directly to the post-review commit phase. This is true for both
    the legacy (no-policy) path and the policy-driven path: REVIEW_CLEAN means
    the review analysis step was bypassed, so we should not route through
    review_analysis (which would expect a review_analysis_decision artifact
    that was never produced). Instead, we advance directly to review_commit.
    """
    if policy is not None:
        # Policy path: advance directly to review_commit, bypassing review_analysis.
        # REVIEW_CLEAN is emitted precisely when review was SKIPPED and no
        # review_analysis_decision artifact was ever produced. Routing through
        # on_success would land on review_analysis, which would then fail when
        # trying to parse the non-existent decision artifact.
        next_phase = "review_commit"
        new_state, effects = _advance_phase(state, next_phase, policy)
        new_state = new_state.copy_with(review_issues_found=False)
        return new_state, effects

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
        new_state, effects = _advance_phase(state, "fix")
        return new_state.copy_with(review_issues_found=True), effects

    new_state, effects = _advance_phase(state, "review_commit")
    return new_state.copy_with(review_issues_found=True), effects


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

    return _advance_phase(state, PHASE_REVIEW)


def _handle_fix_failure(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle fix failure."""
    if policy is not None:
        try:
            next_phase = resolve_next_phase(state.phase, "failure", policy)
            if next_phase == PHASE_FAILED:
                failure_reason = _failure_reason(state, "Fix phase failed")
                return _enter_failed_recovery(state, failure_reason)
            return _advance_phase(state, next_phase, policy)
        except ValueError as exc:
            return _advance_to_terminal(
                state, PHASE_FAILED, f"Routing error after fix failure in '{state.phase}': {exc}"
            )

    if state.reviewer_pass + 1 < state.total_reviewer_passes:
        return _advance_phase(state, "fix")

    return _advance_phase(state, "review_commit")


def _handle_commit_success(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle successful commit."""
    if policy is not None:
        try:
            next_phase = resolve_post_commit_phase(state, policy)
            new_state, effects = _advance_phase(state, next_phase, policy)
            return progress.apply_commit_outcome(state, new_state, skipped=False), effects
        except ValueError as exc:
            return _advance_to_terminal(
                state, PHASE_FAILED, f"Routing error after commit success in '{state.phase}': {exc}"
            )

    new_state, effects = _advance_phase(state, PHASE_COMPLETE)
    return progress.apply_commit_outcome(state, new_state, skipped=False), effects


def _handle_commit_skipped(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle a skipped commit (no diff to commit).

    Advances phase routing exactly like a successful commit so the pipeline
    does not stall, but does NOT increment iteration or reviewer_pass because
    no meaningful agent activity occurred during the skipped phase.
    """
    if policy is not None:
        try:
            next_phase = resolve_post_commit_phase(state, policy)
            new_state, effects = _advance_phase(state, next_phase, policy)
            return progress.apply_commit_outcome(state, new_state, skipped=True), effects
        except ValueError as exc:
            return _advance_to_terminal(
                state, PHASE_FAILED, f"Routing error after commit skipped in '{state.phase}': {exc}"
            )

    new_state, effects = _advance_phase(state, PHASE_COMPLETE)
    return progress.apply_commit_outcome(state, new_state, skipped=True), effects


def _handle_commit_failure(state: PipelineState) -> tuple[PipelineState, list[Effect]]:
    """Handle commit failure."""
    failure_reason = _failure_reason(state, "Commit failed")
    return _enter_failed_recovery(state, failure_reason)


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
    """Handle pipeline failure.

    Uses state.last_error if available and descriptive, which should have been
    set by the preceding failure event handler. Falls back to a descriptive
    message only as a last resort.
    """
    last_error = _failure_reason(
        state,
        (
            f"Pipeline terminated in phase='{state.phase}' with no explicit error; "
            "check upstream last_error propagation"
        ),
    )
    return _enter_failed_recovery(state, last_error)


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
    new_state = progress.advance_phase(state, target_phase, policy=policy)
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
    if terminal == PHASE_FAILED:
        return _enter_failed_recovery(state, reason)

    updates: dict[str, object] = {
        "phase": terminal,
        "previous_phase": state.phase,
    }
    new_state = state.copy_with(**updates)
    return new_state, []


def _handle_fan_out_started(state: PipelineState) -> tuple[PipelineState, list[Effect]]:
    if not state.work_units or state.worker_states:
        return state, []
    new_worker_states = {
        unit.unit_id: WorkerState(unit_id=unit.unit_id, status=WorkerStatus.PENDING)
        for unit in state.work_units
    }
    return state.copy_with(worker_states=new_worker_states), []


def _handle_workers_resumed(state: PipelineState) -> tuple[PipelineState, list[Effect]]:
    if not state.worker_states:
        return state, []
    resumed_states = {
        unit_id: (
            worker_state.copy_with(status=WorkerStatus.PENDING)
            if worker_state.status == WorkerStatus.RUNNING
            else worker_state
        )
        for unit_id, worker_state in state.worker_states.items()
    }
    return state.copy_with(worker_states=resumed_states), []


def _handle_worker_started(
    state: PipelineState,
    event: WorkerStartedEvent,
) -> tuple[PipelineState, list[Effect]]:
    if event.unit_id not in state.worker_states:
        return state, []
    updated = state.worker_states[event.unit_id].copy_with(
        status=WorkerStatus.RUNNING, started_at=datetime.now(UTC)
    )
    return state.copy_with(worker_states={**state.worker_states, event.unit_id: updated}), []


def _handle_worker_completed(
    state: PipelineState,
    event: WorkerCompletedEvent,
) -> tuple[PipelineState, list[Effect]]:
    if event.unit_id not in state.worker_states:
        return state, []
    updated = state.worker_states[event.unit_id].copy_with(
        status=WorkerStatus.SUCCEEDED,
        exit_code=event.exit_code,
        commit_sha=event.commit_sha,
        finished_at=datetime.now(UTC),
    )
    return state.copy_with(worker_states={**state.worker_states, event.unit_id: updated}), []


def _handle_worker_failed(
    state: PipelineState,
    event: WorkerFailedEvent,
) -> tuple[PipelineState, list[Effect]]:
    if event.unit_id not in state.worker_states:
        return state, []
    updated = state.worker_states[event.unit_id].copy_with(
        status=WorkerStatus.FAILED,
        exit_code=event.exit_code,
        error_message=event.error,
        finished_at=datetime.now(UTC),
    )
    return state.copy_with(worker_states={**state.worker_states, event.unit_id: updated}), []


def _handle_all_workers_complete(state: PipelineState) -> tuple[PipelineState, list[Effect]]:
    if not state.worker_states or any(
        ws.status != WorkerStatus.SUCCEEDED for ws in state.worker_states.values()
    ):
        return state, []
    return state.copy_with(phase=PHASE_MERGE_INTEGRATION), []


def _handle_workers_merge_conflict(
    state: PipelineState,
    event: WorkersMergeConflictEvent,
) -> tuple[PipelineState, list[Effect]]:
    unit_ids_str = ", ".join(event.conflicting_unit_ids)
    failure_reason = f"Merge conflict in workers: {unit_ids_str}"
    return _enter_failed_recovery(state, failure_reason)
