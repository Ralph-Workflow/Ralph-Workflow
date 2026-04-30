"""Pure reducer: (state, event, policy) -> (new_state, effects).

No I/O, no side effects, fully deterministic.

This module implements the core state machine for the Ralph pipeline.
Given the current state, an event, and the loaded policy, it computes
the new state and any effects to execute.

The reducer is a PURE FUNCTION — it contains no I/O operations,
no logging, and no mutable state. This makes it fully deterministic
and easy to test.

Routing is driven by the policy: phase transitions come from pipeline.toml,
not hardcoded match arms. All workflow semantics are expressed in policy.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ralph.config.enums import PipelinePhase
from ralph.pipeline import progress
from ralph.pipeline.effects import Effect, SaveCheckpointEffect
from ralph.pipeline.events import (
    AnalysisDecisionEvent,
    Event,
    PhaseFailureEvent,
    PipelineEvent,
    PostFanoutVerificationEvent,
    WorkerCompletedEvent,
    WorkerFailedEvent,
    WorkerStartedEvent,
)
from ralph.pipeline.handoffs import resolve_next_phase, resolve_post_commit_phase
from ralph.pipeline.state import CommitState, PipelineState
from ralph.pipeline.worker_state import WorkerState, WorkerStatus
from ralph.policy.models import PhaseDefinition, PhaseLoopPolicy

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.policy.models import PipelinePolicy
    from ralph.recovery.controller import RecoveryController


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
    state: PipelineState,
    event: Event,
    recovery: RecoveryController | None = None,
    policy: PipelinePolicy | None = None,
) -> tuple[PipelineState, list[Effect]] | None:
    """Handle worker events, returning None if the event is not a worker event.

    When a RecoveryController is supplied, WorkerFailedEvent is routed through it for
    classification-aware recovery (intelligent attribution, budget management). This ensures
    worker failures are attributed to the phase's active agent and can trigger retry or
    fallover behavior.

    Terminal worker events (WorkerStartedEvent, WorkerCompletedEvent) do not go through
    RecoveryController as they represent normal lifecycle events.
    """
    if isinstance(event, WorkerStartedEvent):
        new_state, effects = _handle_worker_started(state, event)
        return _restore_work_units(state, new_state), effects
    if isinstance(event, WorkerCompletedEvent):
        new_state, effects = _handle_worker_completed(state, event)
        return _restore_work_units(state, new_state), effects

    # Worker failure events route through RecoveryController when available
    # for proper attribution and recovery decision-making.
    if isinstance(event, WorkerFailedEvent):
        if recovery is not None:
            # Route through RecoveryController for classification and attribution.
            # The phase from state is used since worker failures are attributed
            # to the current phase's active agent.
            phase_failure = PhaseFailureEvent(
                phase=state.phase,
                reason=event.error or f"Worker {event.unit_id} failed: exit code {event.exit_code}",
                recoverable=True,
            )
            new_state, effects, _ = recovery.handle(
                state,
                phase_failure.reason,
                phase=phase_failure.phase,
                agent=state.current_agent(),
            )
            # Also mark the individual worker as FAILED in state.
            updated = state.worker_states[event.unit_id].copy_with(
                status=WorkerStatus.FAILED,
                exit_code=event.exit_code,
                error_message=event.error,
                finished_at=datetime.now(UTC),
            )
            new_state = new_state.copy_with(
                worker_states={**new_state.worker_states, event.unit_id: updated}
            )
            return _restore_work_units(state, new_state), effects
        # No recovery controller - use legacy direct handling
        new_state, effects = _handle_worker_failed(state, event)
        return _restore_work_units(state, new_state), effects

    return None


def reduce(  # noqa: PLR0911
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
        pipeline_policy: Pipeline policy for resolving transitions.
            Required for all routing decisions. Passing None causes routing
            handlers to route to the policy-declared failure route rather
            than silently falling back to hardcoded behavior.
        recovery: Optional RecoveryController. When supplied, PhaseFailureEvents
            and worker failure events are delegated to it for classification-aware
            recovery (intelligent attribution, budget management). When None,
            the legacy retry/fallback logic is used.

    Returns:
        Tuple of (new_state, effects). Effects are instructions for the
        effect handler to execute.
    """
    # Handle PostFanoutVerificationEvent before worker events.
    if isinstance(event, PostFanoutVerificationEvent):
        if not event.success:
            error_msg = event.error or f"workspace verification failed (exit code {event.exit_code})"  # noqa: E501
            recovered, _ = _enter_failed_recovery(state, error_msg, pipeline_policy)
            return _restore_work_units(state, recovered), []
        return state, []

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
                retry_in_session=event.retry_in_session,
            )
            return _restore_work_units(state, new_state), effects
        return _handle_phase_failure(state, event, policy=pipeline_policy)

    # Handle AnalysisDecisionEvent: route directly through decisions[decision].target.
    # This is the preferred path for analysis routing. The legacy ANALYSIS_SUCCESS/
    # ANALYSIS_LOOPBACK enum events route via transitions.on_success/on_loopback only.
    if isinstance(event, AnalysisDecisionEvent):
        return _handle_analysis_decision(state, event, pipeline_policy)

    # Handle worker events with a unified approach.
    # Pass recovery to enable classification-aware handling for failure events.
    worker_result = _dispatch_worker_event(state, event, recovery, policy=pipeline_policy)
    if worker_result is not None:
        return worker_result

    handlers: dict[
        PipelineEvent,
        Callable[[PipelineState, PipelinePolicy | None], tuple[PipelineState, list[Effect]]],
    ] = {
        PipelineEvent.AGENT_SUCCESS: _handle_agent_success,
        PipelineEvent.AGENT_FAILURE: _handle_agent_failure,
        PipelineEvent.AGENT_RETRY: _ignore_policy(_handle_agent_retry),
        PipelineEvent.ANALYSIS_SUCCESS: _handle_analysis_success,
        PipelineEvent.ANALYSIS_LOOPBACK: _handle_analysis_loopback,
        PipelineEvent.REVIEW_CLEAN: _handle_review_clean,
        PipelineEvent.REVIEW_ISSUES_FOUND: _handle_review_issues_found,
        PipelineEvent.FIX_SUCCESS: _handle_fix_success,
        PipelineEvent.FIX_FAILURE: _handle_fix_failure,
        PipelineEvent.COMMIT_SUCCESS: _handle_commit_success,
        PipelineEvent.COMMIT_SKIPPED: _handle_commit_skipped,
        PipelineEvent.COMMIT_FAILURE: _handle_commit_failure,
        PipelineEvent.CHECKPOINT_SAVED: _ignore_policy(_handle_checkpoint_saved),
        PipelineEvent.CONTEXT_CLEANED: _return_state,
        PipelineEvent.INTERRUPTED: _ignore_policy(_handle_interrupted),
        PipelineEvent.COMPLETE: _handle_complete,
        PipelineEvent.FAILED: _handle_failed,
        PipelineEvent.PHASE_ADVANCE: _handle_phase_advance,
        PipelineEvent.FAN_OUT_STARTED: _ignore_policy(_handle_fan_out_started),
        PipelineEvent.WORKERS_RESUMED: _ignore_policy(_handle_workers_resumed),
        PipelineEvent.ALL_WORKERS_COMPLETE: _handle_all_workers_complete,
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


def _terminal_failure_route(policy: PipelinePolicy | None) -> str:
    """Resolve the terminal failure route from policy.

    Raises:
        RuntimeError: When policy is None (routing requires loaded policy).
    """
    if policy is None:
        raise RuntimeError(
            "Routing requires loaded policy; no fallback to legacy phase constants. "
            "Ensure pipeline_policy is passed to reduce()."
        )
    return policy.recovery.failed_route


def _terminal_success_route(policy: PipelinePolicy | None) -> str:
    """Resolve the terminal success route from policy.

    Raises:
        RuntimeError: When policy is None (routing requires loaded policy).
    """
    if policy is None:
        raise RuntimeError(
            "Routing requires loaded policy; no fallback to legacy phase constants. "
            "Ensure pipeline_policy is passed to reduce()."
        )
    return policy.terminal_phase


def _enter_failed_recovery(
    state: PipelineState,
    reason: str,
    policy: PipelinePolicy | None = None,
) -> tuple[PipelineState, list[Effect]]:
    """Transition to the policy-declared terminal failure route."""
    new_state = state.copy_with(
        phase=_terminal_failure_route(policy),
        previous_phase=state.phase,
        last_error=reason,
        recovery_epoch=state.recovery_epoch + 1,
    )
    return new_state, []


def _handle_phase_failure(
    state: PipelineState,
    event: PhaseFailureEvent,
    policy: PipelinePolicy | None = None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle PhaseFailureEvent from phase handlers.

    PhaseFailureEvent carries a recoverable flag:
    - recoverable=True: route through _handle_agent_failure retry/fallback logic
    - recoverable=False: route directly to the terminal failure route (terminal agent decision)

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
        # _handle_agent_failure preserves it when it transitions to the failure route.
        state_with_error = state.copy_with(last_error=failure_message)
        if event.retry_in_session and state.last_agent_session_id:
            state_with_error = state_with_error.copy_with(session_preserve_retry_pending=True)
        return _handle_agent_failure(state_with_error, policy=policy)
    # Non-recoverable failures enter centralized recovery using the policy-declared route.
    return _enter_failed_recovery(state, failure_message, policy=policy)


def _handle_agent_success(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle successful agent completion."""
    if state.last_retry_delay_ms > 0:
        state = state.copy_with(last_retry_delay_ms=0)
    if policy is None:
        return _advance_to_failed(state, "No policy loaded for agent success routing", policy)

    phase_def = policy.phases.get(state.phase)
    if phase_def is None:
        return _advance_to_failed(state, f"Unknown phase: {state.phase}", policy)

    if phase_def.role == "commit" and not state.commit.agent_invoked:
        updated_commit = CommitState(
            message_prepared=state.commit.message_prepared,
            diff_prepared=state.commit.diff_prepared,
            agent_invoked=True,
        )
        return state.copy_with(commit=updated_commit), []

    if phase_def.role == "analysis":
        return _handle_analysis_success(state, policy)

    return _resolve_or_terminal(state, "success", policy, "agent success")


def _handle_agent_failure(
    state: PipelineState,
    policy: PipelinePolicy | None = None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle agent failure with retry/fallback logic."""
    chain = state.chain_for_phase(state.phase)
    if chain is None:
        failure_reason = _failure_reason(state, f"No tracked agent chain for {state.phase}")
        return _enter_failed_recovery(state, failure_reason, policy)

    max_retries = 3
    if chain.retries < max_retries:
        new_chain = chain.with_retry_increment()
        new_metrics = state.metrics.with_retry_increment()
        new_state = state.with_phase_chain(state.phase, new_chain).copy_with(metrics=new_metrics)
        return new_state, []

    if chain.current_index + 1 < len(chain.agents):
        new_chain = chain.with_advance()
        new_metrics = state.metrics.with_fallback_increment()
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
    return _enter_failed_recovery(state, failure_reason, policy)


def _handle_agent_retry(state: PipelineState) -> tuple[PipelineState, list[Effect]]:
    """Handle agent retry request."""
    chain = state.chain_for_phase(state.phase)
    if chain is None:
        failure_reason = _failure_reason(state, f"No tracked agent chain for {state.phase}")
        return _enter_failed_recovery(state, failure_reason)

    new_chain = chain.with_retry_increment()
    new_metrics = state.metrics.with_continuation_increment()

    new_state = state.with_phase_chain(state.phase, new_chain).copy_with(metrics=new_metrics)
    return new_state, []


def _handle_analysis_success(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle successful analysis decision (continue/approve).

    Routing is driven exclusively through transitions.on_success via resolve_next_phase.
    Decision keys in phase_def.decisions are for vocabulary validation only; the
    reducer does not inspect them for routing.
    """
    if policy is None:
        return _advance_to_failed(state, "No policy loaded for analysis success routing", policy)

    try:
        next_phase = resolve_next_phase(state.phase, "success", policy)
        new_state, effects = _advance_phase(state, next_phase, policy)
        return progress.apply_analysis_success(state, new_state, policy=policy), effects
    except ValueError as exc:
        return _advance_to_failed(
            state,
            f"Routing error after analysis success in '{state.phase}': {exc}",
            policy,
        )


def _handle_analysis_loopback(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle analysis loopback decision (retry/request changes) — policy-driven."""
    if policy is None:
        return _advance_to_failed(state, "No policy loaded for analysis loopback routing", policy)

    phase_def = policy.phases.get(state.phase)
    if phase_def is None:
        return _advance_to_failed(
            state, f"Unknown phase for analysis loopback: {state.phase}", policy
        )

    if isinstance(phase_def.loop_policy, PhaseLoopPolicy):
        return _handle_capped_analysis_loopback_policy_driven(state, policy, phase_def)

    return _resolve_or_terminal(state, "loopback", policy, "analysis loopback")


def _handle_capped_analysis_loopback_policy_driven(
    state: PipelineState,
    policy: PipelinePolicy,
    phase_def: PhaseDefinition,
) -> tuple[PipelineState, list[Effect]]:
    """Handle analysis loopback using policy-declared loop_policy.

    Progress tracking (iteration counter, review_issues_found) is applied
    before routing so that even when routing fails the counters are persisted.
    The runtime cap comes from state.get_max_loop_iteration (set from config),
    not from loop_policy.max_iterations.

    Loopback target comes exclusively from transitions.on_loopback via
    resolve_next_phase — decision keys are vocabulary contracts, not routing keys.
    """
    loop_policy = phase_def.loop_policy
    if not isinstance(loop_policy, PhaseLoopPolicy):
        return _resolve_or_terminal(state, "loopback", policy, "analysis loopback")

    iteration_field: str = loop_policy.iteration_state_field
    current_iteration = state.get_loop_iteration(iteration_field)
    _cap: int | None = state.loop_caps.get(iteration_field)
    if _cap is None:
        if iteration_field == "development_analysis_iteration":
            _cap = state.max_development_analysis_iterations
        elif iteration_field == "review_analysis_iteration":
            _cap = state.max_review_analysis_iterations
        else:
            _cap = loop_policy.max_iterations
    max_iterations: int = _cap
    # Apply progress tracking up front so it is preserved even if routing fails.
    clamped = max(0, min(current_iteration + 1, max_iterations))
    progress_state = progress.apply_analysis_loopback(state, state, iteration_field)
    progress_state = progress_state.with_loop_iteration(iteration_field, clamped)
    if loop_policy.loopback_review_outcome is not None:
        progress_state = progress_state.copy_with(
            review_outcome=loop_policy.loopback_review_outcome
        )

    # Routing target comes from transitions.on_loopback only — no decision-key lookup.
    try:
        loopback_target = resolve_next_phase(state.phase, "loopback", policy)
    except ValueError as exc:
        return _advance_to_failed(
            progress_state,
            f"Routing error for analysis loopback in '{state.phase}': {exc}",
            policy,
        )

    new_state, effects = _advance_phase(progress_state, loopback_target, policy)
    return new_state, effects


def _handle_analysis_decision(
    state: PipelineState,
    event: AnalysisDecisionEvent,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle AnalysisDecisionEvent: route directly through decisions[decision].target.

    The decision string from the agent artifact is used as a key into
    ``phase_def.decisions`` to look up the target phase. This replaces the
    legacy collapsing of all decisions into ANALYSIS_SUCCESS/ANALYSIS_LOOPBACK
    followed by routing via transitions.on_success/on_loopback.

    When the route's reset_loop is True, the loop counter is reset to 0.
    When reset_loop is False and the phase has a loop_policy, the counter
    is incremented (clamped to the runtime cap).
    """
    if policy is None:
        return _advance_to_failed(
            state,
            "No policy loaded for analysis decision routing",
            policy,
        )

    phase_def = policy.phases.get(event.phase)
    if phase_def is None:
        return _advance_to_failed(
            state,
            f"Unknown phase for analysis decision: {event.phase}",
            policy,
        )

    route = phase_def.decisions.get(event.decision)
    if route is None:
        return _advance_to_failed(
            state,
            f"Phase '{event.phase}' has no policy route for decision "
            f"'{event.decision}'. Add it to phases.{event.phase}.decisions "
            f"or update the artifact decision_vocabulary.",
            policy,
        )

    # Apply loop counter accounting before routing.
    progress_state = state
    if phase_def.loop_policy is not None:
        iteration_field = phase_def.loop_policy.iteration_state_field
        if route.reset_loop:
            # Reset counter on forward exit (e.g., 'completed' decision).
            progress_state = progress_state.with_loop_iteration(iteration_field, 0)
        else:
            # Increment counter on loopback (e.g., 'request_changes' decision).
            current = state.get_loop_iteration(iteration_field)
            max_iter = state.get_max_loop_iteration(iteration_field)
            clamped = max(0, min(current + 1, max_iter))
            progress_state = progress_state.with_loop_iteration(iteration_field, clamped)
            # Apply loopback_review_outcome when configured and this is a loopback route.
            lp = phase_def.loop_policy
            if lp.loopback_review_outcome is not None:
                progress_state = progress_state.copy_with(
                    review_outcome=lp.loopback_review_outcome
                )

    # Resolve target: route to terminal failure when target is the failed route,
    # otherwise advance to the declared target.
    failed_route = _terminal_failure_route(policy)
    if route.target == failed_route or route.target not in policy.phases:
        failure_reason = _failure_reason(
            state,
            f"Analysis decision '{event.decision}' in phase '{event.phase}' "
            f"routes to terminal failure target '{route.target}'",
        )
        return _enter_failed_recovery(progress_state, failure_reason, policy)

    new_state, effects = _advance_phase(progress_state, route.target, policy)
    return _restore_work_units(state, new_state), effects


def _handle_review_clean(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle clean review (no issues found).

    When a review is clean, the phase emits a bypass directly to the target
    declared in bypass_routes[clean_outcome]. The bypass key is read from the
    phase's policy-declared clean_outcome field, not from a hardcoded string.
    Falls back to on_success routing when clean_outcome is not set or has no
    matching bypass_routes entry.
    """
    if policy is None:
        return _advance_to_failed(state, "No policy loaded for review clean routing", policy)

    phase_def = policy.phases.get(state.phase)
    if (
        phase_def is not None
        and phase_def.clean_outcome is not None
        and phase_def.clean_outcome in phase_def.bypass_routes
    ):
        next_phase = phase_def.bypass_routes[phase_def.clean_outcome]
        new_state, effects = _advance_phase(state, next_phase, policy)
        return new_state.copy_with(review_outcome=None), effects

    new_state, effects = _resolve_or_terminal(state, "success", policy, "review clean")
    return new_state.copy_with(review_outcome=None), effects


def _handle_review_issues_found(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle review with issues found.

    The review_outcome label is read from the phase's policy-declared
    issues_outcome field. Policy completeness validation rejects review phases
    that omit issues_outcome, so this field is always set when this handler runs.
    """
    if policy is None:
        return _advance_to_failed(state, "No policy loaded for review issues found routing", policy)
    phase_def = policy.phases.get(state.phase)
    if phase_def is None:
        return _advance_to_failed(
            state, f"Unknown phase for review issues found: {state.phase}", policy
        )
    if phase_def.issues_outcome is None:
        return _advance_to_failed(
            state,
            f"Phase '{state.phase}' has role='review' but issues_outcome is not declared. "
            "Add issues_outcome to the phase in pipeline.toml. "
            "See docs/migration/policy-v2.md.",
            policy,
        )
    new_state, effects = _resolve_or_terminal(state, "loopback", policy, "review issues found")
    return new_state.copy_with(review_outcome=phase_def.issues_outcome), effects


def _handle_fix_success(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle successful fix."""
    if policy is None:
        return _advance_to_failed(state, "No policy loaded for fix success routing", policy)
    return _resolve_or_terminal(state, "success", policy, "fix success")


def _handle_fix_failure(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle fix failure."""
    if policy is None:
        return _advance_to_failed(state, "No policy loaded for fix failure routing", policy)
    try:
        next_phase = resolve_next_phase(state.phase, "failure", policy)
        failed_route = _terminal_failure_route(policy)
        # Route to terminal failure if the transition targets a terminal failure pseudo-phase
        # or the policy-declared terminal failure route.
        if next_phase == failed_route or next_phase not in policy.phases:
            failure_reason = _failure_reason(state, "Fix phase failed")
            return _enter_failed_recovery(state, failure_reason, policy)
        return _advance_phase(state, next_phase, policy)
    except ValueError as exc:
        return _advance_to_failed(
            state, f"Routing error after fix failure in '{state.phase}': {exc}", policy
        )


def _handle_commit_success(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle successful commit."""
    if policy is None:
        return _advance_to_failed(state, "No policy loaded for commit success routing", policy)
    try:
        progress_state = progress.apply_commit_outcome(
            state, state, skipped=False, policy=policy
        )
        next_phase = resolve_post_commit_phase(progress_state, policy)
        new_state, effects = _advance_phase(progress_state, next_phase, policy)
        return new_state, effects
    except ValueError as exc:
        return _advance_to_failed(
            state, f"Routing error after commit success in '{state.phase}': {exc}", policy
        )


def _handle_commit_skipped(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle a skipped commit (no diff to commit).

    Advances phase routing exactly like a successful commit so the pipeline
    does not stall, but does NOT increment iteration or reviewer_pass because
    no meaningful agent activity occurred during the skipped phase.
    """
    if policy is None:
        return _advance_to_failed(state, "No policy loaded for commit skipped routing", policy)
    try:
        progress_state = progress.apply_commit_outcome(
            state, state, skipped=True, policy=policy
        )
        next_phase = resolve_post_commit_phase(progress_state, policy)
        new_state, effects = _advance_phase(progress_state, next_phase, policy)
        return new_state, effects
    except ValueError as exc:
        return _advance_to_failed(
            state, f"Routing error after commit skipped in '{state.phase}': {exc}", policy
        )


def _handle_commit_failure(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle commit failure."""
    failure_reason = _failure_reason(state, "Commit failed")
    return _enter_failed_recovery(state, failure_reason, policy)


def _handle_checkpoint_saved(state: PipelineState) -> tuple[PipelineState, list[Effect]]:
    """Handle checkpoint saved event."""
    new_state = state.copy_with(checkpoint_saved_count=state.checkpoint_saved_count + 1)
    return new_state, []


def _handle_interrupted(state: PipelineState) -> tuple[PipelineState, list[Effect]]:
    """Handle user interruption."""
    new_state = state.copy_with(interrupted_by_user=True)
    return new_state, [SaveCheckpointEffect()]


def _handle_complete(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle pipeline completion — routes to the policy-declared terminal success phase."""
    terminal = _terminal_success_route(policy)
    new_state = state.copy_with(phase=terminal)
    return new_state, []


def _handle_failed(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
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
    return _enter_failed_recovery(state, last_error, policy)


def _handle_phase_advance(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle explicit phase advance request."""
    if policy is None:
        return _advance_to_failed(state, "No policy loaded for phase advance routing", policy)
    return _resolve_or_terminal(state, "success", policy, "phase advance")


def _advance_phase(
    state: PipelineState,
    target_phase: PipelinePhase,
    policy: PipelinePolicy | None = None,
) -> tuple[PipelineState, list[Effect]]:
    """Advance to a new phase with proper state resets.

    Args:
        state: Current pipeline state.
        target_phase: Phase to advance to.
        policy: Pipeline policy for drain resolution and commit detection.

    Returns:
        Tuple of (new_state, effects).
    """
    new_state = progress.advance_phase(state, target_phase, policy=policy)
    return new_state, []


def _advance_to_failed(
    state: PipelineState,
    reason: str,
    policy: PipelinePolicy | None = None,
) -> tuple[PipelineState, list[Effect]]:
    """Transition into the terminal failure route via centralized recovery bookkeeping."""
    return _enter_failed_recovery(state, reason, policy)


def _resolve_or_terminal(
    state: PipelineState,
    signal: str,
    policy: PipelinePolicy,
    label: str,
) -> tuple[PipelineState, list[Effect]]:
    try:
        next_phase = resolve_next_phase(state.phase, signal, policy)
    except ValueError as exc:
        return _advance_to_failed(
            state,
            f"Routing error after {label} in '{state.phase}': {exc}",
            policy,
        )
    return _advance_phase(state, next_phase, policy)


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


def _handle_all_workers_complete(
    state: PipelineState,
    policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    if not state.worker_states:
        return state, []

    failed_unit_ids = sorted(
        uid
        for uid, ws in state.worker_states.items()
        if ws.status in (WorkerStatus.FAILED, WorkerStatus.CANCELLED)
    )
    if failed_unit_ids:
        reason = f"Parallel fan-out had failed workers: {', '.join(failed_unit_ids)}"
        return _enter_failed_recovery(state, reason, policy)

    if any(ws.status != WorkerStatus.SUCCEEDED for ws in state.worker_states.values()):
        return state, []

    if policy is None:
        return _advance_to_failed(
            state, "No policy loaded for all-workers-complete routing", policy
        )
    try:
        next_phase = resolve_next_phase(state.phase, "success", policy)
        return _advance_phase(state, next_phase, policy)
    except ValueError:
        return _advance_to_failed(
            state,
            f"No 'success' transition defined in phase '{state.phase}' for all-workers-complete",
            policy,
        )
