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

import functools
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ralph.config.enums import PipelinePhase
from loguru import logger

from ralph.pipeline import progress
from ralph.pipeline._failure_formatting import (
    classified_failure_reason_for_event,
    commit_failure_reason,
)
from ralph.pipeline._reducer_worker_state import (
    handle_fan_out_started as _handle_fan_out_started,
)
from ralph.pipeline._reducer_worker_state import (
    handle_worker_completed as _handle_worker_completed,
)
from ralph.pipeline._reducer_worker_state import (
    handle_worker_failed as _handle_worker_failed,
)
from ralph.pipeline._reducer_worker_state import (
    handle_worker_started as _handle_worker_started,
)
from ralph.pipeline._reducer_worker_state import (
    handle_workers_resumed as _handle_workers_resumed,
)
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.agent_retry_intent import (
    AgentRetryIntent,
    cleared_agent_retry_intent,
    resume_agent_retry_intent,
)
from ralph.pipeline.cycle_timing import (
    RoutingTiming,
    apply_cycle_timebox,
    conclude_cycle_on_route_out_of_cycle,
    start_cycle_for_bypassed_start_source,
)
from ralph.pipeline.effects import Effect, SaveCheckpointEffect
from ralph.pipeline.events import (
    AnalysisDecisionEvent,
    Event,
    ExecutionResultEvent,
    PhaseFailureEvent,
    PipelineEvent,
    PostFanoutVerificationEvent,
    WorkerCompletedEvent,
    WorkerFailedEvent,
    WorkerStartedEvent,
)
from ralph.pipeline.handoffs import (
    commit_closes_a_cycle,
    declared_forward_cycle_outcome,
    resolve_exhausted_analysis_bypass,
    resolve_next_phase,
    resolve_post_commit_phase,
)
from ralph.pipeline.loopback import handle_capped_phase_loopback_policy_driven
from ralph.pipeline.state import CommitState, PipelineState
from ralph.pipeline.worker_state import WorkerStatus
from ralph.policy.explain import explain_routing_decision
from ralph.policy.models import PhaseLoopPolicy
from ralph.recovery.classifier import ClassifiedFailure, FailureContext

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.pipeline.exhausted_analysis_bypass_result import ExhaustedAnalysisBypassResult
    from ralph.policy.models import PipelinePolicy
    from ralph.recovery.agent_selection import AgentSelection
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
    routing_timing: RoutingTiming | None = None,
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
        # Bounded by the deadline like every other failure route into the
        # controller: its retries stay in the same phase, so a spent cycle
        # would otherwise buy another full-length invocation here too.
        expired = redirect_expired_cycle_in_place(state, policy, routing_timing)
        if expired is not None:
            return expired
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
                FailureContext(phase=phase_failure.phase, agent=state.current_agent()),
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


def _reduce_post_fanout_verification(
    state: PipelineState,
    event: PostFanoutVerificationEvent,
    pipeline_policy: PipelinePolicy | None,
) -> tuple[PipelineState, list[Effect]]:
    if not event.success:
        error_msg = event.error or f"workspace verification failed (exit code {event.exit_code})"
        recovered, _ = _enter_failed_recovery(state, error_msg, pipeline_policy)
        return _restore_work_units(state, recovered), []
    return state, []


def _reduce_phase_failure(
    state: PipelineState,
    event: PhaseFailureEvent,
    pipeline_policy: PipelinePolicy | None,
    recovery: RecoveryController | None,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
    if recovery is not None:
        # Ask the deadline BEFORE the controller does anything. Its retry and
        # progression routes keep the run in the same phase, crossing no
        # routing boundary, so the deadline would never be consulted -- and a
        # controller is always present in production, which left the guard on
        # the plain failure handler covering only a path tests take.
        expired = redirect_expired_cycle_in_place(state, pipeline_policy, routing_timing)
        if expired is not None:
            return expired
        classified_failure = None
        if event.failure_category is not None:
            raw_message = event.reason or f"(no reason reported for phase={event.phase})"
            classified_failure = ClassifiedFailure(
                category=event.failure_category,
                reason=classified_failure_reason_for_event(event),
                attributed_agent=None,
                attributed_phase=event.phase,
                counts_against_budget=False,
                original_exception=None,
                raw_message=raw_message,
                reset_session=False,
            )
        new_state, effects, _ = recovery.handle(
            state,
            event.reason or f"(no reason reported for phase={event.phase})",
            FailureContext(
                phase=event.phase,
                agent=state.current_agent(),
                retry_in_session=event.retry_in_session,
                classified_failure=classified_failure,
            ),
        )
        if pipeline_policy is not None and new_state.phase != state.phase:
            new_state = progress.apply_execution_cycle_outcome(
                state,
                new_state,
                policy=pipeline_policy,
            )
        return _restore_work_units(state, new_state), effects
    return _handle_phase_failure(
        state, event, policy=pipeline_policy, routing_timing=routing_timing
    )


def _reduce_result_event(
    state: PipelineState,
    event: AnalysisDecisionEvent | ExecutionResultEvent,
    pipeline_policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
    if isinstance(event, AnalysisDecisionEvent):
        return _handle_analysis_decision(state, event, pipeline_policy, routing_timing)
    return _handle_execution_result(state, event, pipeline_policy, routing_timing)


def _reduce_captured_agent_failure(
    state: PipelineState,
    pipeline_policy: PipelinePolicy | None,
    recovery: RecoveryController | None,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]] | None:
    """Delegate typed broken-agent failures to the recovery controller.

    A spent cycle short-circuits to finalization first: the controller's
    routes stay in the same phase, so nothing downstream would consult the
    deadline before starting another full-length invocation.
    """
    intent = state.agent_retry_intent
    if recovery is None or intent.failed_agent_name is None:
        return None
    expired = redirect_expired_cycle_in_place(state, pipeline_policy, routing_timing)
    if expired is not None:
        return expired
    if intent.failure_reason == "BrokenAgentExitError":
        if intent.broken_agent_reason is None:
            return None
        from ralph.agents.invoke._broken_agent_exit_error import BrokenAgentExitError

        failure: Exception = BrokenAgentExitError(
            intent.failed_agent_name,
            reason=intent.broken_agent_reason,
        )
    elif intent.failure_reason == "PiContextExhaustedExitError":
        from ralph.agents.invoke._pi_context_exhausted_exit_error import PiContextExhaustedExitError

        failure = PiContextExhaustedExitError(intent.failed_agent_name)
    else:
        return None
    new_state, effects, _ = recovery.handle(
        state,
        failure,
        FailureContext(phase=state.phase, agent=state.current_agent()),
    )
    if pipeline_policy is not None and new_state.phase != state.phase:
        new_state = progress.apply_execution_cycle_outcome(
            state,
            new_state,
            policy=pipeline_policy,
        )
    return _restore_work_units(state, new_state), effects


_EVENT_HANDLERS: dict[  # bounded-accumulator-ok: static
    PipelineEvent,
    Callable[[PipelineState, PipelinePolicy | None, RoutingTiming | None], tuple[PipelineState, list[Effect]]],
] = {}


def _get_event_handlers() -> dict[
    PipelineEvent,
    Callable[[PipelineState, PipelinePolicy | None, RoutingTiming | None], tuple[PipelineState, list[Effect]]],
]:
    if not _EVENT_HANDLERS:
        _EVENT_HANDLERS.update(
            {
                PipelineEvent.AGENT_SUCCESS: _handle_agent_success,
                PipelineEvent.AGENT_FAILURE: _handle_agent_failure,
                PipelineEvent.AGENT_RETRY: _handle_agent_retry,
                PipelineEvent.ANALYSIS_SUCCESS: _handle_analysis_success,
                PipelineEvent.ANALYSIS_LOOPBACK: _handle_analysis_loopback,
                PipelineEvent.PHASE_LOOPBACK: _handle_phase_loopback,
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
        )
    return _EVENT_HANDLERS


def reduce(
    state: PipelineState,
    event: Event,
    pipeline_policy: PipelinePolicy | None = None,
    recovery: RecoveryController | None = None,
    *,
    routing_timing: RoutingTiming | None = None,
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
    reduced_event: tuple[PipelineState, list[Effect]] | None = None
    if isinstance(event, PostFanoutVerificationEvent):
        reduced_event = _reduce_post_fanout_verification(state, event, pipeline_policy)
    elif isinstance(event, PhaseFailureEvent):
        reduced_event = _reduce_phase_failure(
            state, event, pipeline_policy, recovery, routing_timing
        )
    elif isinstance(event, AnalysisDecisionEvent | ExecutionResultEvent):
        reduced_event = _reduce_result_event(state, event, pipeline_policy, routing_timing)
    if reduced_event is not None:
        return reduced_event
    if event == PipelineEvent.AGENT_FAILURE:
        recovered_agent_failure = _reduce_captured_agent_failure(
            state, pipeline_policy, recovery, routing_timing
        )
        if recovered_agent_failure is not None:
            return recovered_agent_failure
        if recovery is not None:
            new_st, effs = _handle_agent_failure(
                state, pipeline_policy, routing_timing, recovery=recovery
            )
            return _restore_work_units(state, new_st), effs
    worker_result = _dispatch_worker_event(
        state, event, recovery, policy=pipeline_policy, routing_timing=routing_timing
    )
    if worker_result is not None:
        return worker_result
    handler = _get_event_handlers().get(
        cast("PipelineEvent", event)
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    if handler is None:
        return state, []
    new_state, effects = handler(state, pipeline_policy, routing_timing)
    return _restore_work_units(state, new_state), effects


def _ignore_policy(
    handler: Callable[[PipelineState], tuple[PipelineState, list[Effect]]],
) -> Callable[[PipelineState, PipelinePolicy | None, RoutingTiming | None], tuple[PipelineState, list[Effect]]]:
    def wrapper(
        state: PipelineState,
        _policy: PipelinePolicy | None,
        _routing_timing: RoutingTiming | None = None,
    ) -> tuple[PipelineState, list[Effect]]:
        return handler(state)

    return wrapper


def _return_state(
    state: PipelineState,
    _policy: PipelinePolicy | None,
    _routing_timing: RoutingTiming | None = None,
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
    target = _terminal_failure_route(policy)
    if policy is None:
        raise RuntimeError("Routing requires loaded policy")
    logger.bind(component="policy.routing").info(
        explain_routing_decision(state.phase, target, "failure", reason, recovery=True)
    )
    new_state = progress.advance_phase(state, target, policy=policy).copy_with(
        last_error=reason,
        recovery_epoch=state.recovery_epoch + 1,
    )
    # The cycle timer is deliberately NOT concluded here. The recovery failed
    # route is a terminal to this reducer but does not end the run: the effect
    # router turns it back into the phase the run was in, so the run re-enters
    # the same cycle. Concluding switched the deadline off for the rest of a
    # cycle that kept going -- and it could never re-arm, because starting a
    # timer requires an inactive one. The cycle concludes when it routes out
    # for real, or is reported as open at exit if the run dies inside it.
    return progress.apply_execution_cycle_outcome(state, new_state, policy=policy), []


def _handle_phase_failure(
    state: PipelineState,
    event: PhaseFailureEvent,
    policy: PipelinePolicy | None = None,
    *,
    routing_timing: RoutingTiming | None = None,
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
            state_with_error = state_with_error.copy_with(
                agent_retry_intent=resume_agent_retry_intent(
                    state.last_agent_session_id,
                    failure_reason=failure_message,
                ),
            )
        else:
            state_with_error = state_with_error.copy_with(
                agent_retry_intent=cleared_agent_retry_intent()
            )
        if event.skip_same_agent_retries:
            state_with_error = state_with_error.copy_with(
                agent_retry_intent=AgentRetryIntent(
                    failure_reason=failure_message,
                    skip_same_agent_retries=True,
                )
            )
        return _handle_agent_failure(
            state_with_error, policy=policy, routing_timing=routing_timing
        )
    # Non-recoverable failures: check workflow_fallback before global failure route.
    # Policy-declared workflow_fallback takes precedence over recovery.failed_route.
    if policy is not None:
        phase_def = policy.phases.get(event.phase)
        if phase_def is not None and phase_def.workflow_fallback is not None:
            fallback_target = phase_def.workflow_fallback.target
            logger.bind(component="policy.routing").info(
                explain_routing_decision(
                    event.phase,
                    fallback_target,
                    "workflow_fallback",
                    "non-recoverable failure — routing via policy workflow_fallback",
                )
            )
            # Route the fallback through the cycle-timebox guard so every entry
            # into the configured guarded phase enforces the active deadline
            # (FR-4), not only the analysis request_changes loopback.
            advanced_state, advanced_target = _prepare_phase_advance(
                state, fallback_target, policy, routing_timing=routing_timing
            )
            new_state = progress.advance_phase(
                advanced_state, advanced_target, policy=policy
            ).copy_with(
                last_error=failure_message,
            )
            return progress.apply_execution_cycle_outcome(state, new_state, policy=policy), []
    return _enter_failed_recovery(state, failure_message, policy=policy)


def _handle_agent_success(
    state: PipelineState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
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
        return _handle_analysis_success(state, policy, routing_timing)

    return _resolve_or_terminal(state, "success", policy, "agent success", routing_timing=routing_timing)


def redirect_expired_cycle_in_place(
    state: PipelineState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None,
) -> tuple[PipelineState, list[Effect]] | None:
    """Return the finalization advance when re-running this phase is over budget.

    Asks the deadline the same question a routing boundary would, for a
    handler that is about to stay put. ``None`` when the cycle has room, is
    not running, or this phase is not the guarded one — in which case the
    caller proceeds unchanged.
    """
    if policy is None or routing_timing is None:
        return None
    decision = apply_cycle_timebox(
        state, state.phase, policy=policy, routing_timing=routing_timing
    )
    if not decision.redirected:
        return None
    logger.bind(component="policy.routing").warning(decision.redirect_reason)
    return _advance_phase(
        decision.state, decision.target_phase, policy, routing_timing=routing_timing
    )


def _retry_or_fall_over(
    state: PipelineState,
    chain: AgentChainState,
    *,
    selection: AgentSelection | None = None,
) -> tuple[PipelineState, list[Effect]] | None:
    """Retry the current agent, or fall over to the next one in the chain.

    When selection is None, uses default forward-only fallback logic.
    When selection is provided, routes to the selected index or returns None if None.
    """
    if selection is None:
        max_retries = 3
        if chain.retries < max_retries:
            new_chain = chain.with_retry_increment()
            new_metrics = state.metrics.with_retry_increment()
            return state.with_phase_chain(state.phase, new_chain).copy_with(
                metrics=new_metrics
            ), []

        if chain.current_index + 1 < len(chain.agents):
            new_chain = chain.with_advance()
            new_metrics = state.metrics.with_fallback_increment()
            return state.with_phase_chain(state.phase, new_chain).copy_with(
                metrics=new_metrics,
                last_agent_session_id=None,
                agent_retry_intent=cleared_agent_retry_intent(),
            ), []

        return None

    if selection.index == chain.current_index:
        new_chain = chain.with_retry_increment()
        new_metrics = state.metrics.with_retry_increment()
        return state.with_phase_chain(state.phase, new_chain).copy_with(
            metrics=new_metrics,
            is_waiting_state=False,
        ), []

    if selection.index is not None:
        new_chain = AgentChainState(
            agents=chain.agents,
            current_index=selection.index,
            retries=0,
        )
        new_metrics = state.metrics.with_fallback_increment()
        return state.with_phase_chain(state.phase, new_chain).copy_with(
            metrics=new_metrics,
            last_agent_session_id=None,
            agent_retry_intent=cleared_agent_retry_intent(),
            is_waiting_state=False,
        ), []

    return None


def _all_agents_unavailable_wait_state(
    state: PipelineState,
    chain: AgentChainState,
    selection: AgentSelection | None,
    recovery: RecoveryController | None,
) -> tuple[PipelineState, list[Effect]] | None:
    """Build a cooldown wait state when priority selection finds no agent."""
    if (
        selection is None
        or selection.index is not None
        or recovery is None
        or recovery.agents_now_available(state.phase, chain.agents)
    ):
        return None
    wait_ms = recovery.earliest_available_wait_ms(state.phase, chain.agents)
    reason = "all agents unavailable; waiting for cooldown expiry"
    return state.copy_with(
        last_error=reason,
        last_retry_delay_ms=wait_ms,
        is_waiting_state=True,
    ), []


def _selection_attempt(
    state: PipelineState,
    chain: AgentChainState,
    selection: AgentSelection | None,
    recovery: RecoveryController | None,
) -> tuple[PipelineState, list[Effect]] | None:
    """Return a retry, fallover, or cooldown wait state selected for this failure."""
    same_phase_attempt = _retry_or_fall_over(state, chain, selection=selection)
    if same_phase_attempt is not None:
        return same_phase_attempt
    return _all_agents_unavailable_wait_state(state, chain, selection, recovery)


def _handle_agent_failure(
    state: PipelineState,
    policy: PipelinePolicy | None = None,
    routing_timing: RoutingTiming | None = None,
    *,
    recovery: RecoveryController | None = None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle agent failure with retry/fallback logic."""
    chain = state.chain_for_phase(state.phase)
    if chain is None:
        failure_reason = _failure_reason(state, f"No tracked agent chain for {state.phase}")
        return _enter_failed_recovery(state, failure_reason, policy)

    # A retry or a chain fallover keeps the run in the same phase, so no
    # routing boundary is crossed and the deadline -- enforced only at
    # boundaries -- would never be consulted. Each one is a fresh full-length
    # invocation, and three retries per agent across the chain add up to many
    # of them on a budget that is already spent, while the prompt, the
    # tool-result banner and the policy documentation all say the redirect
    # happens INSTEAD of starting another development invocation.
    expired = redirect_expired_cycle_in_place(state, policy, routing_timing)
    if expired is not None:
        return expired

    if state.agent_retry_intent.skip_same_agent_retries:
        return _handle_skip_same_agent_retries(state, chain, policy, recovery)

    selection: AgentSelection | None = None
    if recovery is not None:
        max_retries = 3
        current_agent = (
            chain.agents[chain.current_index]
            if chain.agents and chain.current_index < len(chain.agents)
            else None
        )
        if chain.retries >= max_retries and current_agent is not None:
            recovery.note_retry_exhaustion(state.phase, current_agent)
        selection = recovery.preferred_agent_index(
            state.phase,
            chain.agents,
            current_index=chain.current_index,
            current_allowance_spent=(chain.retries >= max_retries),
        )

    selected_attempt = _selection_attempt(state, chain, selection, recovery)
    if selected_attempt is not None:
        return selected_attempt

    # Chain exhausted: check for per-phase workflow_fallback before global failure route.
    if policy is not None:
        phase_def = policy.phases.get(state.phase)
        if phase_def is not None and phase_def.workflow_fallback is not None:
            fallback_target = phase_def.workflow_fallback.target
            logger.bind(component="policy.routing").info(
                explain_routing_decision(
                    state.phase,
                    fallback_target,
                    "workflow_fallback",
                    "agent chain exhausted — routing via policy workflow_fallback",
                )
            )
            # Route the fallback through the cycle-timebox guard so every entry
            # into the configured guarded phase enforces the active deadline
            # (FR-4), not only the analysis request_changes loopback.
            advanced_state, advanced_target = _prepare_phase_advance(
                state, fallback_target, policy, routing_timing=routing_timing
            )
            new_state = progress.advance_phase(advanced_state, advanced_target, policy=policy)
            return progress.apply_execution_cycle_outcome(state, new_state, policy=policy), []

    failure_reason = _failure_reason(
        state,
        (
            f"Agent chain exhausted in phase='{state.phase}' after "
            f"{chain.retries} retries across {len(chain.agents)} agents"
        ),
    )
    return _enter_failed_recovery(state, failure_reason, policy)


def _handle_skip_same_agent_retries(
    state: PipelineState,
    chain: AgentChainState,
    policy: PipelinePolicy | None,
    recovery: RecoveryController | None,
) -> tuple[PipelineState, list[Effect]]:
    if recovery is not None:
        current_agent = chain.agents[chain.current_index]
        recovery.note_retry_exhaustion(state.phase, current_agent)
        selection = recovery.preferred_agent_index(
            state.phase,
            chain.agents,
            current_index=chain.current_index,
            current_allowance_spent=True,
        )
        selected_attempt = _selection_attempt(state, chain, selection, recovery)
        if selected_attempt is not None:
            return selected_attempt

    if chain.current_index + 1 < len(chain.agents):
        new_chain = chain.with_advance()
        new_metrics = state.metrics.with_fallback_increment()
        new_state = state.with_phase_chain(state.phase, new_chain).copy_with(
            metrics=new_metrics,
            last_agent_session_id=None,
            agent_retry_intent=cleared_agent_retry_intent(),
        )
        return new_state, []
    failure_reason = _failure_reason(
        state,
        (
            f"Agent chain exhausted in phase='{state.phase}' after "
            "context-exhausted agent requested fallback"
        ),
    )
    return _enter_failed_recovery(state, failure_reason, policy)


def _handle_agent_retry(
    state: PipelineState,
    policy: PipelinePolicy | None = None,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle agent retry request.

    A continuation is another full-length invocation of the same phase, so it
    is bounded by the cycle deadline exactly like a failure retry.
    """
    expired = redirect_expired_cycle_in_place(state, policy, routing_timing)
    if expired is not None:
        return expired

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
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle successful analysis decision (continue/approve).

    Routing is driven exclusively through transitions.on_success via resolve_next_phase.
    Decision keys in phase_def.decisions are not consulted for routing — but the
    forward decision's declared counter and cycle outcome ARE applied, because
    leaving this phase forward concludes the cycle exactly as that decision
    would. Without the outcome, post-commit routing at the eventual final
    commit has no verdict to match on.
    """
    if policy is None:
        return _advance_to_failed(state, "No policy loaded for analysis success routing", policy)

    try:
        next_phase = resolve_next_phase(state.phase, "success", policy)
        forward_outcome = declared_forward_cycle_outcome(state.phase, policy)
        stamped = (
            state.copy_with(pending_cycle_outcome=forward_outcome)
            if forward_outcome is not None and state.pending_cycle_outcome is None
            else state
        )
        new_state, effects = _advance_phase(
            stamped, next_phase, policy, routing_timing=routing_timing
        )
        progress_state = progress.apply_analysis_success(stamped, new_state, policy=policy)
        phase_def = policy.phases.get(state.phase)
        completed_route = (
            phase_def.decisions.get("completed")
            if phase_def is not None and phase_def.decisions is not None
            else None
        )
        progress_state = progress.apply_budget_counter_increment(
            state,
            progress_state,
            completed_route.increments_counter if completed_route is not None else None,
        )
        return progress_state, effects
    except ValueError as exc:
        return _advance_to_failed(
            state,
            f"Routing error after analysis success in '{state.phase}': {exc}",
            policy,
        )


def _handle_execution_result(
    state: PipelineState,
    event: ExecutionResultEvent,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
    """Route an execution result while carrying any policy-declared commit override."""
    if policy is None:
        return _advance_to_failed(state, "No policy loaded for execution result routing", policy)
    if event.phase != state.phase:
        return _advance_to_failed(
            state,
            f"Execution result phase '{event.phase}' does not match active phase '{state.phase}'",
            policy,
        )
    phase_def = policy.phases.get(event.phase)
    if phase_def is None or phase_def.role != "execution":
        return _advance_to_failed(
            state,
            f"Execution result references non-execution phase '{event.phase}'",
            policy,
        )
    routed_state = state.copy_with(
        post_commit_phase_override=phase_def.result_status_post_commit.get(event.status),
        last_execution_result_status=event.status,
    )
    new_state, effects = _resolve_or_terminal(routed_state, "success", policy, "execution result", routing_timing=routing_timing)
    return new_state, effects


def _handle_analysis_loopback(
    state: PipelineState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle analysis loopback decision (retry/request changes) — policy-driven."""
    if policy is None:
        return _advance_to_failed(state, "No policy loaded for analysis loopback routing", policy)

    phase_def = policy.phases.get(state.phase)
    if phase_def is None:
        return _advance_to_failed(
            state, f"Unknown phase for analysis loopback: {state.phase}", policy
        )

    if phase_def.role == "analysis" and isinstance(phase_def.loop_policy, PhaseLoopPolicy):
        return handle_capped_phase_loopback_policy_driven(
            state,
            policy,
            phase_def,
            review_outcome=phase_def.loop_policy.loopback_review_outcome,
            advance_to_failed=_advance_to_failed,
            resolve_or_terminal=functools.partial(_resolve_or_terminal, routing_timing=routing_timing),
            advance_phase=functools.partial(_advance_phase, routing_timing=routing_timing),
        )

    return _resolve_or_terminal(state, "loopback", policy, "analysis loopback", routing_timing=routing_timing)


def _handle_phase_loopback(
    state: PipelineState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle generic bounded phase loopback for non-analysis phases."""
    if policy is None:
        return _advance_to_failed(state, "No policy loaded for phase loopback routing", policy)

    phase_def = policy.phases.get(state.phase)
    if phase_def is None:
        return _advance_to_failed(state, f"Unknown phase for loopback: {state.phase}", policy)

    if isinstance(phase_def.loop_policy, PhaseLoopPolicy):
        return handle_capped_phase_loopback_policy_driven(
            state,
            policy,
            phase_def,
            review_outcome=None,
            advance_to_failed=_advance_to_failed,
            resolve_or_terminal=functools.partial(_resolve_or_terminal, routing_timing=routing_timing),
            advance_phase=functools.partial(_advance_phase, routing_timing=routing_timing),
        )

    return _resolve_or_terminal(state, "loopback", policy, "phase loopback", routing_timing=routing_timing)


def _handle_analysis_decision(
    state: PipelineState,
    event: AnalysisDecisionEvent,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
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
            max_iter = progress.resolve_analysis_cap(
                iteration_field,
                policy,
            )
            # Apply loopback_review_outcome when configured and this is a loopback route.
            lp = phase_def.loop_policy
            progress_state = progress.apply_analysis_loopback(
                state,
                progress_state,
                iteration_field,
                max_iterations=max_iter,
                review_outcome=lp.loopback_review_outcome,
                increment=not progress.analysis_loopback_is_already_charged(event.phase, state, policy),
            )

    progress_state = progress.apply_budget_counter_increment(
        state,
        progress_state,
        route.increments_counter,
    )
    if route.cycle_outcome is not None:
        progress_state = progress_state.copy_with(pending_cycle_outcome=route.cycle_outcome)

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

    advanced_state, advanced_target = _prepare_phase_advance(progress_state, route.target, policy, routing_timing=routing_timing)
    logger.bind(component="policy.routing").info(
        explain_routing_decision(event.phase, advanced_target, "decision", event.decision)
    )
    new_state = progress.advance_phase(advanced_state, advanced_target, policy=policy)
    return _restore_work_units(state, new_state), []


def _handle_review_clean(
    state: PipelineState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
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
        logger.bind(component="policy.routing").info(
            explain_routing_decision(
                state.phase, next_phase, "bypass route", phase_def.clean_outcome
            )
        )
        new_state, effects = _advance_phase(state, next_phase, policy, routing_timing=routing_timing)
        return new_state.copy_with(review_outcome=None), effects

    new_state, effects = _resolve_or_terminal(state, "success", policy, "review clean", routing_timing=routing_timing)
    return new_state.copy_with(review_outcome=None), effects


def _handle_review_issues_found(
    state: PipelineState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
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
            "See docs/sphinx/concepts.md.",
            policy,
        )
    new_state, effects = _resolve_or_terminal(state, "loopback", policy, "review issues found", routing_timing=routing_timing)
    return new_state.copy_with(review_outcome=phase_def.issues_outcome), effects


def _handle_fix_success(
    state: PipelineState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle successful fix."""
    if policy is None:
        return _advance_to_failed(state, "No policy loaded for fix success routing", policy)
    return _resolve_or_terminal(state, "success", policy, "fix success", routing_timing=routing_timing)


def _handle_fix_failure(
    state: PipelineState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
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
        return _advance_phase(state, next_phase, policy, routing_timing=routing_timing)
    except ValueError as exc:
        return _advance_to_failed(
            state, f"Routing error after fix failure in '{state.phase}': {exc}", policy
        )


def _handle_commit_success(
    state: PipelineState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle successful commit."""
    if policy is None:
        return _advance_to_failed(state, "No policy loaded for commit success routing", policy)
    try:
        progress_state = progress.apply_commit_outcome(state, state, skipped=False, policy=policy)
        next_phase = resolve_post_commit_phase(progress_state, policy)
        if progress_state.post_commit_phase_override is not None:
            progress_state = progress.consume_post_commit_phase_override(progress_state)
        if commit_closes_a_cycle(state.phase, policy):
            progress_state = progress_state.copy_with(pending_cycle_outcome=None)
        return _advance_through_invocation_gate(
            progress_state, next_phase, policy, routing_timing
        )
    except ValueError as exc:
        return _advance_to_failed(
            state, f"Routing error after commit success in '{state.phase}': {exc}", policy
        )


def _handle_commit_skipped(
    state: PipelineState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle a skipped commit (no diff to commit).

    Advances phase routing exactly like a successful commit so the pipeline
    does not stall, but does NOT increment iteration or reviewer_pass because
    no meaningful agent activity occurred during the skipped phase.
    """
    if policy is None:
        return _advance_to_failed(state, "No policy loaded for commit skipped routing", policy)
    try:
        progress_state = progress.apply_commit_outcome(state, state, skipped=True, policy=policy)
        next_phase = resolve_post_commit_phase(progress_state, policy)
        if progress_state.post_commit_phase_override is not None:
            progress_state = progress.consume_post_commit_phase_override(progress_state)
        if commit_closes_a_cycle(state.phase, policy):
            progress_state = progress_state.copy_with(pending_cycle_outcome=None)
        return _advance_through_invocation_gate(
            progress_state, next_phase, policy, routing_timing
        )
    except ValueError as exc:
        return _advance_to_failed(
            state, f"Routing error after commit skipped in '{state.phase}': {exc}", policy
        )


def _handle_commit_failure(
    state: PipelineState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle commit failure."""
    failure_reason = commit_failure_reason(state)
    return _enter_failed_recovery(state, failure_reason, policy)


def _apply_invocation_gate(
    state: PipelineState,
    next_phase: PipelinePhase,
    policy: PipelinePolicy,
) -> tuple[PipelinePhase, PipelineState, str | None]:
    """Check an analysis phase's invocation gate and adjust routing accordingly.

    When the next phase is an analysis phase with an ``invocation_gate``, the
    cumulative unrounded elapsed seconds for the gate's ``upstream_execution_phase``
    are summed from the current cycle's timing start index. Analysis is entered
    when the cumulative time meets or exceeds ``minimum_elapsed_seconds`` **or**
    the upstream execution result's status is listed in
    ``always_invoke_statuses``. Otherwise analysis is bypassed: routing
    continues to the analysis phase's policy-declared success transition, the
    completed decision's cycle outcome is set, and no analysis count is
    consumed.

    The third element is the gate's upstream execution phase when the gate
    admitted analysis, and ``None`` otherwise — the caller charges the
    analysis-loop cycle once the advance has actually landed on the phase.
    """
    phase_def = policy.phases.get(next_phase)
    if phase_def is None or phase_def.invocation_gate is None:
        return next_phase, state, None

    gate = phase_def.invocation_gate
    always_invoke = state.last_execution_result_status in gate.always_invoke_statuses
    cumulative = progress.cumulative_execution_seconds(state, gate.upstream_execution_phase)

    if always_invoke or cumulative >= gate.minimum_elapsed_seconds:
        # Admitted, but NOT charged here: the charge belongs after the advance
        # has actually landed on the phase. Charging first billed a pass the
        # exhausted-analysis bypass then skipped, and because the skip
        # predicate counts already-charged passes, the budget read as spent one
        # pass early — a declared cap of five ran four times.
        return next_phase, state, gate.upstream_execution_phase

    # Gate denies entry: skip analysis and route to its success transition.
    bypass_target = phase_def.transitions.on_success
    cycle_outcome = declared_forward_cycle_outcome(next_phase, policy)
    # Never outrank a recorded verdict, matching the other stamp sites.
    if cycle_outcome is not None and state.pending_cycle_outcome is None:
        state = state.copy_with(pending_cycle_outcome=cycle_outcome)
    return bypass_target, state, None


def _advance_through_invocation_gate(
    state: PipelineState,
    next_phase: PipelinePhase,
    policy: PipelinePolicy,
    routing_timing: RoutingTiming | None,
) -> tuple[PipelineState, list[Effect]]:
    """Route a post-commit target through its invocation gate, then advance.

    The gate's analysis-loop charge is applied only once the advance has landed
    on the gated phase, so a pass the exhausted-analysis bypass skips is never
    billed.
    """
    next_phase, gated_state, admitted_execution_phase = _apply_invocation_gate(
        state, next_phase, policy
    )
    gated_state = gated_state.copy_with(last_execution_result_status=None)
    new_state, effects = _advance_phase(
        gated_state, next_phase, policy, routing_timing=routing_timing
    )
    if admitted_execution_phase is not None and new_state.phase == next_phase:
        new_state = progress.charge_analysis_cycle_for_gate(
            new_state, admitted_execution_phase, policy=policy
        )
    return new_state, effects


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
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle pipeline completion — routes to the policy-declared terminal success phase."""
    terminal = _terminal_success_route(policy)
    new_state = progress.advance_phase(state, terminal, policy=policy)
    return new_state, []


def _handle_failed(
    state: PipelineState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
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
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
    """Handle explicit phase advance request."""
    if policy is None:
        return _advance_to_failed(state, "No policy loaded for phase advance routing", policy)
    return _resolve_or_terminal(state, "success", policy, "phase advance", routing_timing=routing_timing)


def _prepare_phase_advance(
    state: PipelineState,
    target_phase: PipelinePhase,
    policy: PipelinePolicy | None = None,
    *,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, PipelinePhase]:
    """Resolve the effective phase target before applying the transition."""
    if policy is None:
        return state, target_phase

    # Apply the plan-to-final-commit cycle timebox guard first so every route
    # that can enter the configured guarded phase uses one timing authority.
    timebox = apply_cycle_timebox(
        state, target_phase, policy=policy, routing_timing=routing_timing
    )
    if timebox.redirected and timebox.redirect_reason is not None:
        logger.bind(component="policy.routing").warning(timebox.redirect_reason)
    state = timebox.state
    target_phase = timebox.target_phase

    current_phase_def = policy.phases.get(state.phase)
    if current_phase_def is not None and current_phase_def.role == "analysis":
        try:
            success_target = resolve_next_phase(state.phase, "success", policy)
        except ValueError:
            success_target = None
        if success_target == target_phase:
            bypass = resolve_exhausted_analysis_bypass(state, state.phase, policy)
            if bypass.skipped:
                return _concluded_if_leaving_cycle(
                    _with_bypassed_cycle_timing(
                        bypass,
                        policy,
                        routing_timing,
                        timer_just_started=timebox.timing_started,
                    ),
                    policy,
                )

    bypass = resolve_exhausted_analysis_bypass(state, target_phase, policy)
    return _concluded_if_leaving_cycle(
        _with_bypassed_cycle_timing(
            bypass, policy, routing_timing, timer_just_started=timebox.timing_started
        ),
        policy,
    )


def _concluded_if_leaving_cycle(
    resolved: tuple[PipelineState, PipelinePhase],
    policy: PipelinePolicy,
) -> tuple[PipelineState, PipelinePhase]:
    """End cycle timing whenever the resolved target lies outside the cycle.

    Applied in the shared resolution seam rather than at individual call
    sites: every route out of a cycle — a decision, a bypass, a loopback, a
    plain success transition, a fallback, a post-commit route — funnels
    through here, and one missed site leaves a timer running that can never
    stop, since starting one requires an inactive cycle.
    """
    state, target_phase = resolved
    return conclude_cycle_on_route_out_of_cycle(state, target_phase, policy=policy), target_phase


def _with_bypassed_cycle_timing(
    bypass: ExhaustedAnalysisBypassResult,
    policy: PipelinePolicy,
    routing_timing: RoutingTiming | None,
    *,
    timer_just_started: bool = False,
) -> tuple[PipelineState, PipelinePhase]:
    """Apply a bypass result with the cycle timer re-evaluated on its real target.

    The timebox is applied to the PENDING target, which a bypass can then
    rewrite — so both cycle boundaries have to be reconsidered against the
    target routing actually took. Missing the end boundary left the timer
    running into the next cycle, whose first development entry was then
    redirected on the previous cycle's spent budget; missing the start
    boundary left a cycle entirely untimed.
    """
    if not bypass.skipped or timer_just_started:
        # Nothing was rewritten (the caller's application already saw the real
        # target), or the cycle was just started by that application — whose
        # elapsed seconds still describe the PREVIOUS cycle, so re-judging the
        # fresh one against them would redirect it before it ran at all.
        return bypass.state, bypass.target_phase
    timebox = apply_cycle_timebox(
        bypass.state,
        bypass.target_phase,
        policy=policy,
        routing_timing=routing_timing,
    )
    if timebox.redirected and timebox.redirect_reason is not None:
        logger.bind(component="policy.routing").warning(timebox.redirect_reason)
    return (
        start_cycle_for_bypassed_start_source(
            timebox.state,
            timebox.target_phase,
            tuple(skip.phase for skip in bypass.skipped),
            policy=policy,
        ),
        timebox.target_phase,
    )


def _advance_phase(
    state: PipelineState,
    target_phase: PipelinePhase,
    policy: PipelinePolicy | None = None,
    *,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
    """Advance to a new phase with proper state resets.

    Args:
        state: Current pipeline state.
        target_phase: Phase to advance to.
        policy: Pipeline policy for drain resolution and commit detection.

    Returns:
        Tuple of (new_state, effects).
    """
    advanced_state, advanced_target = _prepare_phase_advance(
        state, target_phase, policy, routing_timing=routing_timing
    )
    new_state = progress.advance_phase(advanced_state, advanced_target, policy=policy)
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
    *,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
    try:
        next_phase = resolve_next_phase(state.phase, signal, policy)
    except ValueError as exc:
        return _advance_to_failed(
            state,
            f"Routing error after {label} in '{state.phase}': {exc}",
            policy,
        )
    advanced_state, advanced_target = _prepare_phase_advance(
        state, next_phase, policy, routing_timing=routing_timing
    )
    logger.bind(component="policy.routing").info(
        explain_routing_decision(state.phase, advanced_target, "signal", signal)
    )
    new_state = progress.advance_phase(advanced_state, advanced_target, policy=policy)
    return new_state, []


def _handle_all_workers_complete(
    state: PipelineState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
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
        return _advance_phase(state, next_phase, policy, routing_timing=routing_timing)
    except ValueError:
        return _advance_to_failed(
            state,
            f"No 'success' transition defined in phase '{state.phase}' for all-workers-complete",
            policy,
        )
