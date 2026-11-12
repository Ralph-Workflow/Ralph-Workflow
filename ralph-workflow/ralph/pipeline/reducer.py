from __future__ import annotations

import functools
import re
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ralph.config.enums import PipelinePhase
from loguru import logger

from ralph.phases._commit_review_events import (
    CommitReviewApproved,
    CommitReviewEvent,
    CommitReviewGateFailed,
    CommitReviewLoopback,
    CommitReviewRejected,
)
from ralph.pipeline import progress
from ralph.pipeline._commit_failure_pending import PendingCommitFailure, consume_failure
from ralph.pipeline._failure_formatting import (
    classified_failure_reason_for_event,
    commit_failure_reason,
)
from ralph.pipeline._fan_out_authority import (
    handle_fan_out_started as _handle_fan_out_started,
)
from ralph.pipeline._fan_out_authority import (
    handle_worker_completed as _handle_worker_completed,
)
from ralph.pipeline._fan_out_authority import (
    handle_worker_failed as _handle_worker_failed,
)
from ralph.pipeline._fan_out_authority import (
    handle_worker_started as _handle_worker_started,
)
from ralph.pipeline._fan_out_authority import (
    handle_workers_resumed as _handle_workers_resumed,
)
from ralph.pipeline._pipeline_state_machine import PhaseDecision, decide_phase_transition
from ralph.pipeline._wait_reason import WaitReason
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.agent_retry_intent import (
    AgentRetryIntent,
    cleared_agent_retry_intent,
    resume_agent_retry_intent,
)
from ralph.pipeline.commit_executor import (
    COMMIT_APPROVAL_MISSING_CODE,
    COMMIT_TREE_SHA_MISMATCH_CODE,
)
from ralph.pipeline.cycle_timing import (
    RoutingTiming,
    apply_cycle_timebox,
    apply_development_timebox,
    conclude_cycle_on_route_out_of_cycle,
    conclude_development_timebox_on_route_out_of_development,
    start_cycle_for_bypassed_start_source,
)
from ralph.pipeline.effects import Effect, SaveCheckpointEffect
from ralph.pipeline.events import (
    AgentFailureEvent,
    AnalysisDecisionEvent,
    CommitResidualEvent,
    Event,
    ExecutionResultEvent,
    PhaseFailureEvent,
    PipelineEvent,
    PostFanoutVerificationEvent,
    WorkerCompletedEvent,
    WorkerFailedEvent,
    WorkerStartedEvent,
    observation_from_event,
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
from ralph.recovery.failure_category import FailureCategory

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.exhausted_analysis_bypass_result import ExhaustedAnalysisBypassResult
    from ralph.policy.models import PhaseDefinition, PipelinePolicy
    from ralph.recovery.agent_selection import AgentSelection
    from ralph.recovery.controller import RecoveryController


def _failure_reason(state: PipelineState, fallback: str) -> str:
    if state.last_error:
        return state.last_error
    return fallback


def _restore_work_units(
    state: PipelineState,
    new_state: PipelineState,
) -> PipelineState:
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
    if isinstance(event, WorkerStartedEvent):
        new_state, effects = _handle_worker_started(state, event)
        return _restore_work_units(state, new_state), effects
    if isinstance(event, WorkerCompletedEvent):
        new_state, effects = _handle_worker_completed(state, event)
        return _restore_work_units(state, new_state), effects

    if isinstance(event, WorkerFailedEvent):
        expired = redirect_expired_cycle_in_place(state, policy, routing_timing)
        if expired is not None:
            return expired
        if recovery is not None:
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
        workspace_changed: bool = getattr(event, "workspace_changed", False)
        new_state, effects, _ = recovery.handle(
            state,
            event.reason or f"(no reason reported for phase={event.phase})",
            FailureContext(
                phase=event.phase,
                agent=state.current_agent(),
                retry_in_session=event.retry_in_session,
                classified_failure=classified_failure,
            ),
            workspace_changed=workspace_changed,
        )
        if pipeline_policy is not None and state.phase != new_state.phase:
            new_state = progress.apply_execution_cycle_outcome(
                state,
                new_state,
                policy=pipeline_policy,
            )
        return _restore_work_units(state, new_state), effects
    return _handle_phase_failure(
        state,
        event,
        policy=pipeline_policy,
        routing_timing=routing_timing,
        failure_category=event.failure_category,
    )


def _reduce_result_event(
    state: PipelineState,
    event: AnalysisDecisionEvent | ExecutionResultEvent,
    pipeline_policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
    recovery: RecoveryController | None = None,
) -> tuple[PipelineState, list[Effect]]:
    if isinstance(event, AnalysisDecisionEvent):
        return _handle_analysis_decision(state, event, pipeline_policy, routing_timing)
    if recovery is not None and isinstance(event, ExecutionResultEvent):
        current_agent = state.current_agent()
        if current_agent:
            from ralph.recovery.agent_failure_ledger import AttemptFact

            recovery.ledger.record(
                AttemptFact(
                    phase=event.phase,
                    agent=current_agent,
                    artifact_valid=True,
                )
            )
    return _handle_execution_result(state, event, pipeline_policy, routing_timing)


def _reduce_captured_agent_failure(
    state: PipelineState,
    pipeline_policy: PipelinePolicy | None,
    recovery: RecoveryController | None,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]] | None:
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
            returncode=intent.broken_agent_returncode or 0,
            stderr=intent.broken_agent_stderr,
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
    if pipeline_policy is not None and state.phase != new_state.phase:
        new_state = progress.apply_execution_cycle_outcome(
            state,
            new_state,
            policy=pipeline_policy,
        )
    return _restore_work_units(state, new_state), effects


_EVENT_HANDLERS: dict[
    PipelineEvent,
    Callable[
        [PipelineState, PipelinePolicy | None, RoutingTiming | None],
        tuple[PipelineState, list[Effect]],
    ],
] = {}


def _get_event_handlers() -> dict[
    PipelineEvent,
    Callable[
        [PipelineState, PipelinePolicy | None, RoutingTiming | None],
        tuple[PipelineState, list[Effect]],
    ],
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
                PipelineEvent.COMMIT_RESIDUAL: _handle_commit_residual,
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


def _route_reduced_decision(
    state: PipelineState,
    new_state: PipelineState,
    pipeline_policy: PipelinePolicy | None,
    integration_record: IntegrationRecord | None = None,
) -> PipelineState:
    rebase = new_state.rebase
    terminal_states: set[str] = set()
    if pipeline_policy is not None:
        from ralph.policy.models import PipelinePolicy

        if isinstance(pipeline_policy, PipelinePolicy):
            terminal_states.update(pipeline_policy.terminal_states())
            if pipeline_policy.terminal_phase:
                terminal_states.add(pipeline_policy.terminal_phase)

    is_phase_boundary = (state.phase != new_state.phase) or (
        bool(terminal_states) and new_state.phase in terminal_states
    )

    if new_state.rebase.last_decision is not None:
        rebase = new_state.rebase.model_copy(update={"last_decision": None})
        new_state = new_state.copy_with(rebase=rebase)

    if (
        state.rebase.last_decision is None
        and not is_phase_boundary
        and integration_record is None
        and not new_state.rebase.integration_unresolved
        and not new_state.rebase.recovery_record_retained
        and not new_state.rebase.is_intervention_required
    ):
        return new_state

    decision = decide_phase_transition(
        new_state,
        pipeline_policy,
        integration_record=integration_record,
        rebase_state=new_state.rebase,
    )
    decision_str = decision.value

    if (
        decision
        in (
            PhaseDecision.BLOCK_FOR_RECOVERY,
            PhaseDecision.REPORT_INTERVENTION_REQUIRED,
        )
        and is_phase_boundary
    ):
        new_state = new_state.copy_with(
            phase=state.phase,
            last_agent_session_id=None,
            agent_retry_intent=cleared_agent_retry_intent(),
        )
        rebase = new_state.rebase

    if rebase.last_decision != decision_str:
        return new_state.copy_with(rebase=rebase.model_copy(update={"last_decision": decision_str}))
    return new_state


def _reduce_agent_failure_pure(
    state: PipelineState,
    event: Event,
    pipeline_policy: PipelinePolicy | None,
    recovery: RecoveryController | None,
    routing_timing: RoutingTiming | None,
) -> tuple[PipelineState, list[Effect]] | None:
    recovered_agent_failure = _reduce_captured_agent_failure(
        state, pipeline_policy, recovery, routing_timing
    )
    if recovered_agent_failure is not None:
        return recovered_agent_failure
    if recovery is not None:
        new_st, effs = _handle_agent_failure(
            state, pipeline_policy, routing_timing, recovery=recovery, event=event
        )
        return _restore_work_units(state, new_st), effs
    return None


def _reduce_pure(
    state: PipelineState,
    event: Event | CommitReviewEvent,
    pipeline_policy: PipelinePolicy | None = None,
    recovery: RecoveryController | None = None,
    *,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
    if isinstance(
        event,
        (
            CommitReviewApproved,
            CommitReviewRejected,
            CommitReviewGateFailed,
            CommitReviewLoopback,
        ),
    ):
        return _reduce_commit_review_event(
            state, event, pipeline_policy, routing_timing=routing_timing, recovery=recovery
        )

    reduced_event: tuple[PipelineState, list[Effect]] | None = None
    if event == PipelineEvent.COMMIT_RESIDUAL:
        reduced_event = (state, [])
    elif isinstance(event, CommitResidualEvent):
        committed = event.committed_paths
        remaining = event.remaining_paths
        if (
            not committed
            or not remaining
            or len(committed) != len(set(committed))
            or len(remaining) != len(set(remaining))
            or not set(committed).isdisjoint(remaining)
        ):
            reduced_event = (state, [])
        else:
            reduced_event = _handle_commit_residual(state, pipeline_policy, routing_timing)
    elif isinstance(event, PostFanoutVerificationEvent):
        reduced_event = _reduce_post_fanout_verification(state, event, pipeline_policy)
    elif isinstance(event, PhaseFailureEvent):
        reduced_event = _reduce_phase_failure(
            state, event, pipeline_policy, recovery, routing_timing
        )
    elif isinstance(event, AnalysisDecisionEvent | ExecutionResultEvent):
        reduced_event = _reduce_result_event(
            state, event, pipeline_policy, routing_timing, recovery=recovery
        )
    if reduced_event is not None:
        return reduced_event
    if event == PipelineEvent.AGENT_FAILURE or isinstance(event, AgentFailureEvent):
        agent_failure_result = _reduce_agent_failure_pure(
            state, event, pipeline_policy, recovery, routing_timing
        )
        if agent_failure_result is not None:
            return agent_failure_result
    worker_result = _dispatch_worker_event(
        state, event, recovery, policy=pipeline_policy, routing_timing=routing_timing
    )
    if worker_result is not None:
        return worker_result
    handler = _get_event_handlers().get(cast("PipelineEvent", event))
    if handler is None:
        return state, []
    new_state, effects = handler(state, pipeline_policy, routing_timing)
    return _restore_work_units(state, new_state), effects


def reduce(
    state: PipelineState,
    event: Event | CommitReviewEvent,
    pipeline_policy: PipelinePolicy | None = None,
    recovery: RecoveryController | None = None,
    *,
    routing_timing: RoutingTiming | None = None,
    integration_record: IntegrationRecord | None = None,
) -> tuple[PipelineState, list[Effect]]:
    new_state, effects = _reduce_pure(
        state,
        event,
        pipeline_policy=pipeline_policy,
        recovery=recovery,
        routing_timing=routing_timing,
    )
    routed_state = _route_reduced_decision(
        state,
        new_state,
        pipeline_policy,
        integration_record=integration_record,
    )
    if recovery is not None and hasattr(recovery, "ledger"):
        try:
            snap = recovery.ledger.snapshot()
            if snap != routed_state.ledger_snapshot:
                routed_state = routed_state.copy_with(ledger_snapshot=snap)
        except Exception:
            pass
    return routed_state, effects


def _ignore_policy(
    handler: Callable[[PipelineState], tuple[PipelineState, list[Effect]]],
) -> Callable[
    [PipelineState, PipelinePolicy | None, RoutingTiming | None], tuple[PipelineState, list[Effect]]
]:
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
    if policy is None:
        raise RuntimeError(
            "Routing requires loaded policy; no fallback to legacy phase constants. "
            "Ensure pipeline_policy is passed to reduce()."
        )
    return policy.recovery.failed_route


def _terminal_success_route(policy: PipelinePolicy | None) -> str:
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
    *,
    failure_category: FailureCategory | None = None,
) -> tuple[PipelineState, list[Effect]]:
    target = _terminal_failure_route(policy)
    if policy is None:
        raise RuntimeError("Routing requires loaded policy")
    logger.bind(component="policy.routing").info(
        explain_routing_decision(state.phase, target, "failure", reason, recovery=True)
    )
    rebase = state.rebase
    retry_delay_ms = 0
    if failure_category is FailureCategory.INTEGRATION:
        rebase = rebase.advance_conflict_strategy(reason)
        if not rebase.resolution_exhausted:
            retry_delay_ms = min(1000 * (1 << rebase.conflict_strategy_index), 10_000)
    new_state = progress.advance_phase(state, target, policy=policy).copy_with(
        last_error=reason,
        recovery_epoch=state.recovery_epoch + 1,
        rebase=rebase,
        last_retry_delay_ms=retry_delay_ms,
        pending_cycle_outcome=None,
    )
    if failure_category is FailureCategory.INTEGRATION:
        resume_phase = state.phase if target != state.phase else state.previous_phase
        new_state = new_state.copy_with(
            phase=resume_phase or policy.entry_phase,
            previous_phase=state.previous_phase,
        )
    return progress.apply_execution_cycle_outcome(state, new_state, policy=policy), []


def _workflow_fallback_target(
    phase_name: str,
    phase_def: PhaseDefinition,
    policy: PipelinePolicy,
) -> str | None:
    fallback = phase_def.workflow_fallback
    if fallback is None:
        return None
    target = fallback.target
    if isinstance(target, str) and target in policy.phases:
        return target
    logger.bind(component="policy.routing").warning(
        "Ignoring workflow_fallback for phase={phase}: target {target!r} is not a declared phase",
        phase=phase_name,
        target=target,
    )
    return None


def _handle_phase_failure(
    state: PipelineState,
    event: PhaseFailureEvent,
    policy: PipelinePolicy | None = None,
    *,
    routing_timing: RoutingTiming | None = None,
    failure_category: FailureCategory | None = None,
) -> tuple[PipelineState, list[Effect]]:
    if event.reason and event.reason.strip():
        failure_message = f"{event.phase}: {event.reason}"
    else:
        failure_message = f"(no reason reported for phase={event.phase})"

    if event.recoverable:
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
            state_with_error,
            policy=policy,
            routing_timing=routing_timing,
            failure_category=event.failure_category,
            event=event,
        )
    if policy is not None:
        phase_def = policy.phases.get(event.phase)
        fallback_target = (
            _workflow_fallback_target(event.phase, phase_def, policy)
            if phase_def is not None
            else None
        )
        if fallback_target is not None:
            logger.bind(component="policy.routing").info(
                explain_routing_decision(
                    event.phase,
                    fallback_target,
                    "workflow_fallback",
                    "non-recoverable failure — routing via policy workflow_fallback",
                )
            )
            advanced_state, advanced_target = _prepare_phase_advance(
                state, fallback_target, policy, routing_timing=routing_timing
            )
            new_state = progress.advance_phase(
                advanced_state, advanced_target, policy=policy
            ).copy_with(
                last_error=failure_message,
            )
            return progress.apply_execution_cycle_outcome(state, new_state, policy=policy), []
    return _enter_failed_recovery(
        state, failure_message, policy=policy, failure_category=failure_category
    )


def _handle_agent_success(
    state: PipelineState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
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

    return _resolve_or_terminal(
        state, "success", policy, "agent success", routing_timing=routing_timing
    )


def redirect_expired_cycle_in_place(
    state: PipelineState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None,
) -> tuple[PipelineState, list[Effect]] | None:
    if policy is None or routing_timing is None:
        return None
    decision = apply_development_timebox(
        state, state.phase, policy=policy, routing_timing=routing_timing
    )
    if decision.redirected:
        logger.bind(component="policy.routing").warning(decision.redirect_reason)
        advanced, effects = _advance_phase(
            decision.state, decision.target_phase, policy, routing_timing=routing_timing
        )
        return advanced, effects
    if state.dev_timebox_active:
        return None
    decision = apply_cycle_timebox(state, state.phase, policy=policy, routing_timing=routing_timing)
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
    selection: AgentSelection,
) -> tuple[PipelineState, list[Effect]] | None:
    if selection.index == chain.current_index:
        new_chain = chain.with_retry_increment()
        new_metrics = state.metrics.with_retry_increment()
        return state.with_phase_chain(state.phase, new_chain).clear_waiting().copy_with(
            metrics=new_metrics,
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
        ).clear_waiting(), []

    return None


def _all_agents_unavailable_wait_state(
    state: PipelineState,
    chain: AgentChainState,
    selection: AgentSelection,
    recovery: RecoveryController | None,
) -> tuple[PipelineState, list[Effect]] | None:
    if (
        selection.index is not None
        or recovery is None
        or recovery.agents_now_available(state.phase, chain.agents)
    ):
        return None
    wait_ms = recovery.earliest_available_wait_ms(state.phase, chain.agents)
    reason = "all agents unavailable; waiting for cooldown expiry"
    return state.copy_with(
        last_error=reason,
        last_retry_delay_ms=wait_ms,
    ).set_waiting(WaitReason.ALL_AGENTS_UNAVAILABLE, None), []


def _selection_attempt(
    state: PipelineState,
    chain: AgentChainState,
    selection: AgentSelection,
    recovery: RecoveryController | None,
) -> tuple[PipelineState, list[Effect]] | None:
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
    failure_category: FailureCategory | None = None,
    event: Event | None = None,
) -> tuple[PipelineState, list[Effect]]:
    chain = state.chain_for_phase(state.phase)
    if chain is None:
        failure_reason = _failure_reason(state, f"No tracked agent chain for {state.phase}")
        return _enter_failed_recovery(state, failure_reason, policy)

    expired = redirect_expired_cycle_in_place(state, policy, routing_timing)
    if expired is not None:
        return expired

    if recovery is None:
        return _fail_closed_without_owner(
            state,
            chain,
            policy,
            routing_timing,
            failure_category,
        )

    return _handle_owned_agent_failure(
        state,
        chain,
        policy,
        routing_timing,
        recovery,
        failure_category,
        event,
    )


def _handle_owned_agent_failure(
    state: PipelineState,
    chain: AgentChainState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None,
    recovery: RecoveryController,
    failure_category: FailureCategory | None,
    event: Event | None,
) -> tuple[PipelineState, list[Effect]]:
    if state.agent_retry_intent.skip_same_agent_retries:
        return _handle_skip_same_agent_retries(state, chain, policy, recovery)

    current_agent = (
        chain.agents[chain.current_index]
        if chain.agents and chain.current_index < len(chain.agents)
        else None
    )
    if current_agent is not None:
        observation = observation_from_event(event)
        if observation is None or not observation.already_charged:
            workspace_changed: bool = (
                observation.resolved_workspace_changed() if observation is not None else False
            )
            demoted_reason = recovery.record_attempt_fact(
                state.phase,
                current_agent,
                artifact_valid=False,
                workspace_changed=workspace_changed,
                free_retry_category=True,
            )
            if demoted_reason is not None and len(chain.agents) == 1:
                logger.warning(demoted_reason)
                return _enter_failed_recovery(state, demoted_reason, policy)
    selection = recovery.preferred_agent_index(
        state.phase,
        chain.agents,
        current_index=chain.current_index,
    )

    selected_attempt = _selection_attempt(state, chain, selection, recovery)
    if selected_attempt is not None:
        return selected_attempt

    return _handle_chain_exhausted(
        state,
        chain,
        policy,
        routing_timing,
        failure_category,
    )


def _fail_closed_without_owner(
    state: PipelineState,
    chain: AgentChainState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None,
    failure_category: FailureCategory | None,
) -> tuple[PipelineState, list[Effect]]:
    current_agent = (
        chain.agents[chain.current_index]
        if chain.agents and chain.current_index < len(chain.agents)
        else None
    )
    detail = _failure_reason(state, f"agent failure in phase '{state.phase}'")
    reason = (
        f"{detail}: no recovery owner is reachable to judge agent '{current_agent}' "
        f"in phase '{state.phase}', so the phase fails closed instead of "
        "granting another attempt"
    )
    return _handle_chain_exhausted(
        state,
        chain,
        policy,
        routing_timing,
        failure_category,
        reason=reason,
    )


def _handle_chain_exhausted(
    state: PipelineState,
    chain: AgentChainState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None,
    failure_category: FailureCategory | None,
    *,
    reason: str | None = None,
) -> tuple[PipelineState, list[Effect]]:
    if policy is not None:
        phase_def = policy.phases.get(state.phase)
        fallback_target = (
            _workflow_fallback_target(state.phase, phase_def, policy)
            if phase_def is not None
            else None
        )
        if fallback_target is not None:
            logger.bind(component="policy.routing").info(
                explain_routing_decision(
                    state.phase,
                    fallback_target,
                    "workflow_fallback",
                    "agent chain exhausted — routing via policy workflow_fallback",
                )
            )
            advanced_state, advanced_target = _prepare_phase_advance(
                state, fallback_target, policy, routing_timing=routing_timing
            )
            new_state = progress.advance_phase(advanced_state, advanced_target, policy=policy)
            return progress.apply_execution_cycle_outcome(state, new_state, policy=policy), []

    failure_reason = (
        reason
        if reason is not None
        else _failure_reason(
            state,
            (
                f"Agent chain exhausted in phase='{state.phase}' after "
                f"{chain.retries} retries across {len(chain.agents)} agents"
            ),
        )
    )
    return _enter_failed_recovery(state, failure_reason, policy, failure_category=failure_category)


def _handle_skip_same_agent_retries(
    state: PipelineState,
    chain: AgentChainState,
    policy: PipelinePolicy | None,
    recovery: RecoveryController | None,
) -> tuple[PipelineState, list[Effect]]:
    if recovery is not None:
        selection = recovery.preferred_agent_index(
            state.phase,
            chain.agents,
            current_index=chain.current_index,
            current_allowance_spent=True,
        )
        selected_attempt = _selection_attempt(state, chain, selection, recovery)
        if selected_attempt is not None:
            return selected_attempt

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
    if policy is None:
        return _advance_to_failed(state, "No policy loaded for execution result routing", policy)
    if state.phase != event.phase:
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
    new_state, effects = _resolve_or_terminal(
        routed_state, "success", policy, "execution result", routing_timing=routing_timing
    )
    return new_state, effects


def _handle_analysis_loopback(
    state: PipelineState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
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
            resolve_or_terminal=functools.partial(
                _resolve_or_terminal, routing_timing=routing_timing
            ),
            advance_phase=functools.partial(_advance_phase, routing_timing=routing_timing),
        )

    return _resolve_or_terminal(
        state, "loopback", policy, "analysis loopback", routing_timing=routing_timing
    )


def _handle_phase_loopback(
    state: PipelineState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
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
            resolve_or_terminal=functools.partial(
                _resolve_or_terminal, routing_timing=routing_timing
            ),
            advance_phase=functools.partial(_advance_phase, routing_timing=routing_timing),
        )

    return _resolve_or_terminal(
        state, "loopback", policy, "phase loopback", routing_timing=routing_timing
    )


def _handle_analysis_decision(
    state: PipelineState,
    event: AnalysisDecisionEvent,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
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

    progress_state = state
    if phase_def.loop_policy is not None:
        iteration_field = phase_def.loop_policy.iteration_state_field
        if route.reset_loop:
            progress_state = progress_state.with_loop_iteration(iteration_field, 0)
        else:
            max_iter = progress.resolve_analysis_cap(
                iteration_field,
                policy,
            )
            lp = phase_def.loop_policy
            progress_state = progress.apply_analysis_loopback(
                state,
                progress_state,
                iteration_field,
                max_iterations=max_iter,
                review_outcome=lp.loopback_review_outcome,
                increment=not progress.analysis_loopback_is_already_charged(
                    event.phase, state, policy
                ),
            )

    progress_state = progress.apply_budget_counter_increment(
        state,
        progress_state,
        route.increments_counter,
    )
    if route.cycle_outcome is not None:
        progress_state = progress_state.copy_with(pending_cycle_outcome=route.cycle_outcome)

    failed_route = _terminal_failure_route(policy)
    if route.target == failed_route or route.target not in policy.phases:
        failure_reason = _failure_reason(
            state,
            f"Analysis decision '{event.decision}' in phase '{event.phase}' "
            f"routes to terminal failure target '{route.target}'",
        )
        return _enter_failed_recovery(progress_state, failure_reason, policy)

    advanced_state, advanced_target = _prepare_phase_advance(
        progress_state, route.target, policy, routing_timing=routing_timing
    )
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
        new_state, effects = _advance_phase(
            state, next_phase, policy, routing_timing=routing_timing
        )
        return new_state.copy_with(review_outcome=None), effects

    new_state, effects = _resolve_or_terminal(
        state, "success", policy, "review clean", routing_timing=routing_timing
    )
    return new_state.copy_with(review_outcome=None), effects


def _handle_review_issues_found(
    state: PipelineState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
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
    new_state, effects = _resolve_or_terminal(
        state, "loopback", policy, "review issues found", routing_timing=routing_timing
    )
    return new_state.copy_with(review_outcome=phase_def.issues_outcome), effects


def _handle_fix_success(
    state: PipelineState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
    if policy is None:
        return _advance_to_failed(state, "No policy loaded for fix success routing", policy)
    return _resolve_or_terminal(
        state, "success", policy, "fix success", routing_timing=routing_timing
    )


def _handle_fix_failure(
    state: PipelineState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
    if policy is None:
        return _advance_to_failed(state, "No policy loaded for fix failure routing", policy)
    try:
        next_phase = resolve_next_phase(state.phase, "failure", policy)
        failed_route = _terminal_failure_route(policy)
        if failed_route == next_phase or next_phase not in policy.phases:
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
    if policy is None:
        return _advance_to_failed(state, "No policy loaded for commit success routing", policy)
    try:
        progress_state = progress.apply_commit_outcome(state, state, skipped=False, policy=policy)
        progress_state = progress_state.copy_with(approved_tree_sha=None)
        next_phase = resolve_post_commit_phase(progress_state, policy)
        has_override = progress_state.post_commit_phase_override is not None
        if has_override:
            progress_state = progress.consume_post_commit_phase_override(progress_state)
        if commit_closes_a_cycle(state.phase, policy):
            progress_state = progress_state.copy_with(pending_cycle_outcome=None)
        if has_override:
            return _advance_phase(progress_state, next_phase, policy, routing_timing=routing_timing)
        return _advance_through_invocation_gate(progress_state, next_phase, policy, routing_timing)
    except ValueError as exc:
        return _advance_to_failed(
            state, f"Routing error after commit success in '{state.phase}': {exc}", policy
        )


def _handle_commit_residual(
    state: PipelineState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
    if policy is None:
        return _advance_to_failed(state, "No policy loaded for residual commit routing", policy)
    return _advance_phase(state, state.phase, policy, routing_timing=routing_timing)


def _handle_commit_skipped(
    state: PipelineState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
    if policy is None:
        return _advance_to_failed(state, "No policy loaded for commit skipped routing", policy)
    try:
        progress_state = progress.apply_commit_outcome(state, state, skipped=True, policy=policy)
        progress_state = progress_state.copy_with(approved_tree_sha=None)
        next_phase = resolve_post_commit_phase(progress_state, policy)
        has_override = progress_state.post_commit_phase_override is not None
        if has_override:
            progress_state = progress.consume_post_commit_phase_override(progress_state)
        if commit_closes_a_cycle(state.phase, policy):
            progress_state = progress_state.copy_with(pending_cycle_outcome=None)
        if has_override:
            return _advance_phase(progress_state, next_phase, policy, routing_timing=routing_timing)
        return _advance_through_invocation_gate(progress_state, next_phase, policy, routing_timing)
    except ValueError as exc:
        return _advance_to_failed(
            state, f"Routing error after commit skipped in '{state.phase}': {exc}", policy
        )


def _reduce_commit_review_rejection(
    state: PipelineState,
    event: CommitReviewRejected,
    policy: PipelinePolicy,
    phase_def: PhaseDefinition,
    *,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
    new_rejections = state.commit_review_rejections + 1
    loop_counter_cfg = policy.loop_counters.get("commit_review_rejections")
    max_rejections = (
        loop_counter_cfg.default_max
        if loop_counter_cfg is not None
        else policy.commit_review.max_rejections
    )

    timebox_expired = False
    ct = policy.cycle_timebox
    if (
        ct is not None
        and routing_timing is not None
        and state.cycle_timebox_active
        and routing_timing.total_elapsed_seconds >= ct.duration_seconds
    ):
        timebox_expired = True

    if new_rejections >= max_rejections or timebox_expired:
        if timebox_expired:
            reason = f"Cycle timebox expired on commit review rejection: {event.reason}"
        elif new_rejections > max_rejections:
            reason = (
                f"Commit review rejection limit exceeded ({new_rejections}/{max_rejections}): "
                f"{event.reason}"
            )
        else:
            reason = (
                f"Commit review rejection limit reached ({new_rejections}/{max_rejections}); "
                f"the run fails at the limit without another rollback: {event.reason}"
            )
        failed_state = state.copy_with(
            commit_review_rejections=new_rejections,
            last_error=reason,
            rollback_pending=False,
            rollback_ref=event.ref,
        )
        target = (
            phase_def.transitions.on_failure
            if phase_def.transitions.on_failure
            else policy.recovery.failed_route
        )
        return _advance_phase(
            failed_state,
            target,
            policy,
            routing_timing=routing_timing,
        )

    progress_state = progress.apply_commit_outcome(state, state, skipped=False, policy=policy)
    target_phase = phase_def.transitions.on_reject or "development"
    state_for_advance = progress_state.copy_with(
        commit_review_rejections=new_rejections,
        rollback_pending=True,
        rollback_ref=event.ref,
        last_review_rejection_reason=event.reason,
    )
    return _route_rejection_through_commit_entry(
        state_for_advance,
        policy,
        phase_def,
        target_phase,
        routing_timing=routing_timing,
    )


def _commit_phase_fed_by_review(phase_def: PhaseDefinition, policy: PipelinePolicy) -> str | None:
    commit_phase = phase_def.transitions.on_success
    target_def = policy.phases.get(commit_phase)
    if target_def is None or target_def.role != "commit":
        return None
    return commit_phase


def _route_rejection_through_commit_entry(
    state: PipelineState,
    policy: PipelinePolicy,
    phase_def: PhaseDefinition,
    target_phase: PipelinePhase,
    *,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
    commit_phase = _commit_phase_fed_by_review(phase_def, policy)
    if commit_phase is None:
        return _advance_phase(state, target_phase, policy, routing_timing=routing_timing)
    commit_state, _ = _advance_phase(state, commit_phase, policy, routing_timing=routing_timing)
    return _advance_phase(commit_state, target_phase, policy, routing_timing=routing_timing)


def _reduce_commit_review_event(
    state: PipelineState,
    event: (
        CommitReviewApproved | CommitReviewRejected | CommitReviewGateFailed | CommitReviewLoopback
    ),
    policy: PipelinePolicy | None,
    *,
    routing_timing: RoutingTiming | None = None,
    recovery: RecoveryController | None = None,
) -> tuple[PipelineState, list[Effect]]:
    phase_def = policy.phases.get(state.phase) if policy is not None else None
    if policy is None or phase_def is None:
        err = (
            "No policy loaded for commit review routing"
            if policy is None
            else f"Unknown phase '{state.phase}' for commit review"
        )
        return _advance_to_failed(state, err, policy)

    if isinstance(event, CommitReviewApproved):
        target_phase = phase_def.transitions.on_success
        updated_state = state.copy_with(
            approved_tree_sha=event.tree_sha,
            commit_approval_required=True,
        )
        if phase_def.loop_policy is not None:
            updated_state = updated_state.with_loop_iteration(
                phase_def.loop_policy.iteration_state_field, 0
            )
        return _advance_phase(updated_state, target_phase, policy, routing_timing=routing_timing)

    if isinstance(event, CommitReviewRejected):
        return _reduce_commit_review_rejection(
            state,
            event,
            policy,
            phase_def,
            routing_timing=routing_timing,
        )

    if isinstance(event, CommitReviewGateFailed):
        reason = f"Commit review gate failed: {event.detail}"
        target = (
            phase_def.transitions.on_failure
            if phase_def.transitions.on_failure
            else policy.recovery.failed_route
        )
        failed_state = state.copy_with(last_error=reason, rollback_pending=False)
        return _advance_phase(failed_state, target, policy, routing_timing=routing_timing)

    target = phase_def.transitions.on_loopback if phase_def.transitions.on_loopback else state.phase
    loop_state = state.copy_with(last_error=event.hint)
    if phase_def.loop_policy is not None:
        field = phase_def.loop_policy.iteration_state_field
        loop_state = loop_state.with_loop_iteration(field, loop_state.get_loop_iteration(field) + 1)
    return _advance_phase(loop_state, target, policy, routing_timing=routing_timing)


_MAX_COMMIT_RECOVERY_ATTEMPTS = 3
LOCK_RETRY_DELAY_MS = 5_000

DIRTY_WORKTREE_FAILURE_ENCODING = "worktree not clean"


def _is_repairable_git_failure(reason: str | None) -> bool:
    if not reason:
        return False
    error_text = reason.lower()
    repairable_patterns = (
        "head is detached",
        "detached head",
        "detached",
        "index.lock",
        "head.lock",
        ".lock",
        "stale lock",
        "lock contention",
        "cannot lock ref",
        "unable to create",
        "unmerged",
        "conflict",
        "conflicts",
        "repeated commit failure",
        DIRTY_WORKTREE_FAILURE_ENCODING,
        "uncommitted",
        "staged",
        "gitoperationerror",
        "giterror",
    )
    return any(p in error_text for p in repairable_patterns)


def _is_commit_approval_signal(reason: str | None) -> bool:
    if not reason:
        return False
    return COMMIT_TREE_SHA_MISMATCH_CODE in reason or COMMIT_APPROVAL_MISSING_CODE in reason


def _resolve_commit_recovery_phase(
    state: PipelineState,
    policy: PipelinePolicy,
) -> str | None:
    for name in ("commit_recovery", "COMMIT_RECOVERY"):
        if name in policy.phases and name != state.phase:
            return name
    for name in ("development_commit_review", "DEVELOPMENT_COMMIT_REVIEW"):
        if name in policy.phases and name != state.phase:
            return name
    for name, defn in policy.phases.items():
        if (
            defn.role == "commit_review"
            and name != state.phase
            and not name.endswith("_final_commit_review")
        ):
            return name
    return None


def _recoverable_git_failure_default_target(
    state: PipelineState,
    policy: PipelinePolicy,
    last_error: str | None,
) -> str | None:
    if not _is_repairable_git_failure(last_error):
        return None
    if _resolve_commit_recovery_phase(state, policy) is not None:
        return None
    return state.phase


def _route_lock_contention_in_place(
    state: PipelineState,
    cleared_state: PipelineState,
    policy: PipelinePolicy,
    last_error: str | None,
    failure_reason: str,
    routing_timing: RoutingTiming | None,
) -> tuple[PipelineState, list[Effect]] | None:
    from ralph.git._lock_recovery import names_a_lock_file

    if not names_a_lock_file(last_error or ""):
        return None
    if _resolve_commit_recovery_phase(cleared_state, policy) is not None:
        return None
    in_place_target = cleared_state.phase
    logger.bind(component="policy.routing").warning(
        "Git lock blocked the commit; retrying {} in place after {}ms without consuming "
        "the recovery budget (recovery_epoch={}/{}): {}",
        in_place_target,
        LOCK_RETRY_DELAY_MS,
        cleared_state.recovery_epoch,
        _MAX_COMMIT_RECOVERY_ATTEMPTS,
        failure_reason,
    )
    advanced_state, target = _prepare_phase_advance(
        cleared_state, in_place_target, policy, routing_timing=routing_timing
    )
    advanced = progress.advance_phase(advanced_state, target, policy=policy)
    updates: dict[str, object] = {
        "commit": state.commit,
        "last_error": failure_reason,
        "recovery_epoch": min(cleared_state.recovery_epoch + 1, _MAX_COMMIT_RECOVERY_ATTEMPTS),
        "pending_cycle_outcome": None,
        "last_execution_result_status": "failed",
        "last_retry_delay_ms": LOCK_RETRY_DELAY_MS,
    }
    if target == in_place_target:
        updates["previous_phase"] = state.previous_phase
    return advanced.copy_with(**updates), []


def _pending_commit_failure_belongs_to_state(
    pending: PendingCommitFailure,
    state: PipelineState,
) -> bool:
    if pending.approved_tree_sha != state.approved_tree_sha:
        return False
    if state.commit.agent_invoked:
        return True
    return state.last_error is None or state.last_error == pending.reason


def _handle_commit_failure(
    state: PipelineState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
    if policy is None:
        return _advance_to_failed(state, "No policy loaded for commit failure routing", policy)

    cleared_state = state.copy_with(
        pending_cycle_outcome=None,
        last_execution_result_status="failed",
    )
    pending = consume_failure()
    last_error = (
        pending.reason
        if pending is not None and _pending_commit_failure_belongs_to_state(pending, state)
        else None
    )
    if last_error is None and _is_repairable_git_failure(state.last_error):
        last_error = state.last_error

    approval_signal = _is_commit_approval_signal(last_error)
    if approval_signal or _is_repairable_git_failure(last_error):
        failure_reason = last_error or commit_failure_reason(state)
        if approval_signal:
            logger.bind(component="policy.routing").info(
                "Commit was not made against the approved tree; routing back to commit review: {}",
                failure_reason,
            )
        lock_retry = _route_lock_contention_in_place(
            state, cleared_state, policy, last_error, failure_reason, routing_timing
        )
        if lock_retry is not None:
            return lock_retry
        if cleared_state.recovery_epoch >= _MAX_COMMIT_RECOVERY_ATTEMPTS:
            logger.bind(component="policy.routing").error(
                "commit-recovery counter exhausted (epoch={}, cap={}); "
                "stopping automated recovery after persistent failure: {}",
                cleared_state.recovery_epoch,
                _MAX_COMMIT_RECOVERY_ATTEMPTS,
                failure_reason,
            )
            terminal_state, effects = _enter_failed_recovery(
                cleared_state,
                failure_reason,
                policy,
            )
            return terminal_state.copy_with(
                recovery_epoch=_MAX_COMMIT_RECOVERY_ATTEMPTS,
                last_execution_result_status="failed",
            ), effects

        recovery_phase = _resolve_commit_recovery_phase(cleared_state, policy)
        if recovery_phase is not None:
            logger.bind(component="policy.routing").info(
                explain_routing_decision(
                    cleared_state.phase,
                    recovery_phase,
                    "recovery",
                    failure_reason,
                    recovery=True,
                )
            )
            advanced_state, target = _prepare_phase_advance(
                cleared_state, recovery_phase, policy, routing_timing=routing_timing
            )
            recovery_updates: dict[str, object] = {
                "commit": state.commit,
                "last_error": failure_reason,
                "recovery_epoch": cleared_state.recovery_epoch + 1,
                "pending_cycle_outcome": None,
                "last_execution_result_status": "failed",
            }
            from ralph.git._lock_recovery import names_a_lock_file

            if names_a_lock_file(last_error or ""):
                recovery_updates["last_retry_delay_ms"] = LOCK_RETRY_DELAY_MS
            new_state = progress.advance_phase(advanced_state, target, policy=policy).copy_with(
                **recovery_updates
            )
            return new_state, []

        in_place_target = _recoverable_git_failure_default_target(
            cleared_state,
            policy,
            last_error,
        )
        if in_place_target is not None:
            failure_reason = last_error or commit_failure_reason(state)
            lock_path_str = ""
            _lock_pattern = re.compile(r"Unable to create '([^']+\.lock)'")
            _lock_match = _lock_pattern.search(last_error or "")
            if _lock_match:
                lock_path_str = _lock_match.group(1)
                try:
                    from pathlib import Path as _Path

                    lock_age = time.time() - _Path(lock_path_str).stat().st_mtime
                    lock_path_str = f"{lock_path_str} (age={lock_age:.1f}s)"
                except Exception:
                    pass
            logger.bind(component="policy.routing").warning(
                "Repairable git failure with no recovery phase defined; "
                "re-routing in-place to {} (lock={}) with validated message preserved "
                "at effect.message_file; policy has no commit_recovery/commit_review phase; "
                "recovery_epoch={}/{}",
                in_place_target,
                lock_path_str or last_error,
                cleared_state.recovery_epoch,
                _MAX_COMMIT_RECOVERY_ATTEMPTS,
            )
            advanced_state, target = _prepare_phase_advance(
                cleared_state, in_place_target, policy, routing_timing=routing_timing
            )
            advanced_after_progress = progress.advance_phase(advanced_state, target, policy=policy)
            updates: dict[str, object] = {
                "commit": state.commit,
                "last_error": failure_reason,
                "recovery_epoch": cleared_state.recovery_epoch + 1,
                "pending_cycle_outcome": None,
                "last_execution_result_status": "failed",
            }
            if target == in_place_target:
                updates["previous_phase"] = state.previous_phase
            new_state = advanced_after_progress.copy_with(**updates)
            return new_state, []

    failure_reason = commit_failure_reason(state)
    return _enter_failed_recovery(cleared_state, failure_reason, policy)


def _apply_invocation_gate(
    state: PipelineState,
    next_phase: PipelinePhase,
    policy: PipelinePolicy,
) -> tuple[PipelinePhase, PipelineState, str | None]:
    phase_def = policy.phases.get(next_phase)
    if phase_def is None or phase_def.invocation_gate is None:
        return next_phase, state, None

    gate = phase_def.invocation_gate
    always_invoke = state.last_execution_result_status in gate.always_invoke_statuses
    cumulative = progress.cumulative_execution_seconds(state, gate.upstream_execution_phase)

    if always_invoke or cumulative >= gate.minimum_elapsed_seconds:
        return next_phase, state, gate.upstream_execution_phase

    bypass_target = phase_def.transitions.on_success
    cycle_outcome = declared_forward_cycle_outcome(next_phase, policy)
    if cycle_outcome is not None and state.pending_cycle_outcome is None:
        state = state.copy_with(pending_cycle_outcome=cycle_outcome)
    return bypass_target, state, None


def _advance_through_invocation_gate(
    state: PipelineState,
    next_phase: PipelinePhase,
    policy: PipelinePolicy,
    routing_timing: RoutingTiming | None,
) -> tuple[PipelineState, list[Effect]]:
    next_phase, gated_state, admitted_execution_phase = _apply_invocation_gate(
        state, next_phase, policy
    )
    gated_state = gated_state.copy_with(last_execution_result_status=None)
    new_state, effects = _advance_phase(
        gated_state, next_phase, policy, routing_timing=routing_timing
    )
    if admitted_execution_phase is not None and next_phase == new_state.phase:
        new_state = progress.charge_analysis_cycle_for_gate(
            new_state, admitted_execution_phase, policy=policy
        )
    return new_state, effects


def _handle_checkpoint_saved(state: PipelineState) -> tuple[PipelineState, list[Effect]]:
    new_state = state.copy_with(checkpoint_saved_count=state.checkpoint_saved_count + 1)
    return new_state, []


def _handle_interrupted(state: PipelineState) -> tuple[PipelineState, list[Effect]]:
    new_state = state.copy_with(interrupted_by_user=True)
    return new_state, [SaveCheckpointEffect()]


def _handle_complete(
    state: PipelineState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
    terminal = _terminal_success_route(policy)
    return _advance_phase(state, terminal, policy, routing_timing=routing_timing)


def _handle_failed(
    state: PipelineState,
    policy: PipelinePolicy | None,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
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
    if policy is None:
        return _advance_to_failed(state, "No policy loaded for phase advance routing", policy)
    return _resolve_or_terminal(
        state, "success", policy, "phase advance", routing_timing=routing_timing
    )


def _prepare_phase_advance(
    state: PipelineState,
    target_phase: PipelinePhase,
    policy: PipelinePolicy | None = None,
    *,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, PipelinePhase]:
    if policy is None:
        return state, target_phase

    timebox = apply_cycle_timebox(state, target_phase, policy=policy, routing_timing=routing_timing)
    if timebox.redirected and timebox.redirect_reason is not None:
        logger.bind(component="policy.routing").warning(timebox.redirect_reason)
    state = timebox.state
    target_phase = timebox.target_phase
    development_timebox = apply_development_timebox(
        state, target_phase, policy=policy, routing_timing=routing_timing
    )
    if development_timebox.redirected and development_timebox.redirect_reason is not None:
        logger.bind(component="policy.routing").warning(development_timebox.redirect_reason)
    state = development_timebox.state
    target_phase = development_timebox.target_phase

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
    state, target_phase = resolved
    state = conclude_cycle_on_route_out_of_cycle(state, target_phase, policy=policy)
    return (
        conclude_development_timebox_on_route_out_of_development(
            state, target_phase, policy=policy
        ),
        target_phase,
    )


def _with_bypassed_cycle_timing(
    bypass: ExhaustedAnalysisBypassResult,
    policy: PipelinePolicy,
    routing_timing: RoutingTiming | None,
    *,
    timer_just_started: bool = False,
) -> tuple[PipelineState, PipelinePhase]:
    if not bypass.skipped or timer_just_started:
        return bypass.state, bypass.target_phase
    timebox = apply_cycle_timebox(
        bypass.state,
        bypass.target_phase,
        policy=policy,
        routing_timing=routing_timing,
    )
    if timebox.redirected and timebox.redirect_reason is not None:
        logger.bind(component="policy.routing").warning(timebox.redirect_reason)
    development_timebox = apply_development_timebox(
        timebox.state, timebox.target_phase, policy=policy, routing_timing=routing_timing
    )
    if development_timebox.redirected and development_timebox.redirect_reason is not None:
        logger.bind(component="policy.routing").warning(development_timebox.redirect_reason)
    return (
        start_cycle_for_bypassed_start_source(
            development_timebox.state,
            development_timebox.target_phase,
            tuple(skip.phase for skip in bypass.skipped),
            policy=policy,
            routing_timing=routing_timing,
        ),
        development_timebox.target_phase,
    )


def _should_preserve_commit_retry(
    state: PipelineState,
    target_phase: PipelinePhase,
    policy: PipelinePolicy,
) -> bool:
    source_phase_def = policy.phases.get(state.phase)
    if source_phase_def is None or source_phase_def.role != "commit_review":
        return False
    target_phase_def = policy.phases.get(target_phase)
    if target_phase_def is None or target_phase_def.role != "commit":
        return False
    if not state.commit.agent_invoked:
        return False
    return _is_repairable_git_failure(state.last_error)


def _advance_phase(
    state: PipelineState,
    target_phase: PipelinePhase,
    policy: PipelinePolicy | None = None,
    *,
    routing_timing: RoutingTiming | None = None,
) -> tuple[PipelineState, list[Effect]]:
    advanced_state, advanced_target = _prepare_phase_advance(
        state, target_phase, policy, routing_timing=routing_timing
    )
    new_state = progress.advance_phase(advanced_state, advanced_target, policy=policy)
    if policy is not None and _should_preserve_commit_retry(state, advanced_target, policy):
        new_state = new_state.copy_with(
            commit=state.commit,
            recovery_epoch=state.recovery_epoch,
            last_error=state.last_error,
        )
    return new_state, []


def _advance_to_failed(
    state: PipelineState,
    reason: str,
    policy: PipelinePolicy | None = None,
) -> tuple[PipelineState, list[Effect]]:
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
    if signal == "success" and _should_preserve_commit_retry(state, advanced_target, policy):
        new_state = new_state.copy_with(
            commit=state.commit,
            recovery_epoch=state.recovery_epoch,
            last_error=state.last_error,
        )
    elif signal == "loopback":
        source_phase_def = policy.phases.get(state.phase)
        target_phase_def = policy.phases.get(advanced_target)
        if (
            source_phase_def is not None
            and source_phase_def.role == "commit_review"
            and target_phase_def is not None
            and target_phase_def.role == "commit_review"
        ):
            new_state = new_state.copy_with(
                recovery_epoch=state.recovery_epoch,
                last_error=state.last_error,
            )
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
