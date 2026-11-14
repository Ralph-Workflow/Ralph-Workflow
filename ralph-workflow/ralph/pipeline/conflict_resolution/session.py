from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke import AgentInactivityTimeoutError, SupervisionInfrastructureError
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.agent_entry_point import (
    AgentEntryPoint,
    register_entry_point,
    registered_entry_points,
)
from ralph.pipeline.attempt_runner import (
    build_entry_point_recovery_controller,
    run_agent_attempt,
)
from ralph.pipeline.attempt_runner_deps import PRODUCTION_INVOKE, AttemptRunnerDeps
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.attempt_fault import (
    classify_ralph_origin_fault,
)
from ralph.pipeline.conflict_resolution.chain_candidates import session_ledger
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.effect_executor import (
    AGENT_NOT_FOUND_REASON,
    pop_last_captured_retry_intent,
)
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.recovery.conflict_attempt_outcome import ConflictAttemptOutcome

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from ralph.config.gitlink_policy_config import GitlinkPolicyConfig
    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.attempt_result import AttemptResult
    from ralph.pipeline.factory import PipelineDeps
    from ralph.policy.models import PolicyBundle
    from ralph.recovery.agent_failure_ledger import AgentFailureLedger
    from ralph.recovery.agent_unavailability_tracker import UnavailabilityStore
    from ralph.recovery.classified_failure import ClassifiedFailure
    from ralph.recovery.controller import RecoveryController
    from ralph.workspace.probe import WorkspaceChangeProbe
    from ralph.workspace.protocol import Workspace
    from ralph.workspace.scope import WorkspaceScope

__all__ = [
    "ATTEMPT_FAILED_EVIDENCE",
    "ResolutionSession",
    "ResolutionTerminationReason",
    "begin_resolution_stop",
    "classify_failed_resolution_attempt",
    "conflict_chain_max_retries",
    "ensure_session_controller",
    "invoke_resolution_agent",
    "resolution_chain_agents",
]


def _run_conflict_resolution_attempt(
    *_args: object,
    effect: object | None = None,
    invocation: object | None = None,
    config: UnifiedConfig,
    controller: RecoveryController,
    deps: AttemptRunnerDeps,
    state: object | None = None,
    **execute_kwargs: object,
) -> AttemptResult:
    return run_agent_attempt(
        AgentEntryPoint.CONFLICT_RESOLUTION,
        effect=effect,
        invocation=invocation,
        config=config,
        controller=controller,
        deps=deps,
        state=state,
        **execute_kwargs,
    )


if AgentEntryPoint.CONFLICT_RESOLUTION not in registered_entry_points():
    register_entry_point(
        AgentEntryPoint.CONFLICT_RESOLUTION,
        _run_conflict_resolution_attempt,
    )

_MIN_FALLBACK_CHAIN_LENGTH = 2

ATTEMPT_FAILED_EVIDENCE = "conflict attempt failed"

_effect_executor_module = importlib.import_module("ralph.pipeline.effect_executor")


def _invoke_effect(target: object, *args: object, **kwargs: object) -> object:
    config = kwargs.pop("config")
    pipeline_deps = kwargs.pop("pipeline_deps")
    workspace_scope = kwargs.pop("workspace_scope")
    result: object = _effect_executor_module.execute_agent_effect(
        target,
        config,
        pipeline_deps,
        workspace_scope,
        *args,
        **kwargs,
    )
    return result


@dataclass
class ResolutionSession:
    started_at: float | None = None
    unresolved_paths: tuple[str, ...] = ()
    inactivity_timeout_seconds: float | None = None
    max_rounds_per_stop: int | None = None
    max_rebase_conflict_stops: int | None = None
    max_fallback_agents: int | None = None
    gitlink_policy: GitlinkPolicyConfig | None = None
    terminal_reason: ResolutionTerminationReason | None = None
    last_activity_kind: str | None = None
    last_activity_at: float | None = None
    last_duration_seconds: float | None = None
    chain_cursor: int = 0
    charge_conflict_budget: bool = True
    dead_tool_surfaces: tuple[str, ...] = ()
    out_of_reach_paths: tuple[str, ...] = ()
    last_recovery_reason: str | None = None
    recovery_controller: RecoveryController | None = None
    unavailability_store: UnavailabilityStore | None = None
    ledger: AgentFailureLedger | None = None
    recovery_state: object | None = None
    last_retry_delay_ms: int = 0
    exhaustion_reason: str | None = None
    operator_action_message: str | None = None
    skip_same_agent_retries: bool = False
    last_attempt_evidence: str | None = None
    last_attempt_failure: BaseException | str | None = None
    last_attempt_outcome: ConflictAttemptOutcome | None = None
    last_attempt_result: AttemptResult | None = None
    last_attempt_saw_activity: bool = False
    rounds_spent: int = 0
    last_failure_cause: ResolutionFailureCause = ResolutionFailureCause.NONE
    workspace: Workspace | None = None
    workspace_probe: WorkspaceChangeProbe | None = None


def begin_resolution_stop(
    session: ResolutionSession,
    *,
    is_strategy_rotation: bool = False,
) -> None:
    from ralph.pipeline.agent_chain_state import AgentChainState
    from ralph.pipeline.state import PipelineState

    session.out_of_reach_paths = ()
    session.skip_same_agent_retries = False
    session.last_attempt_evidence = None
    session.last_attempt_failure = None
    session.last_attempt_outcome = None
    session.last_attempt_saw_activity = False
    session.terminal_reason = None
    session.last_activity_kind = None
    session.last_activity_at = None
    session.last_duration_seconds = None
    session.exhaustion_reason = None
    session.operator_action_message = None
    session.charge_conflict_budget = True

    if not is_strategy_rotation:
        session.chain_cursor = 0
        session.rounds_spent = 0
        session.last_failure_cause = ResolutionFailureCause.NONE
        state = session.recovery_state
        if not isinstance(state, PipelineState):
            return
        chain = state.chain_for_phase(PHASE_RESOLUTION)
        if chain is None:
            return
        session.recovery_state = state.with_phase_chain(
            PHASE_RESOLUTION,
            AgentChainState(agents=list(chain.agents), current_index=0, retries=0),
        )


def resolution_chain_agents(policy_bundle: PolicyBundle) -> tuple[str, ...]:
    if not hasattr(policy_bundle, "agents"):
        return ()
    agents_policy = policy_bundle.agents
    if not hasattr(agents_policy, "agent_drains"):
        return ()
    drain_binding = agents_policy.agent_drains.get(PHASE_RESOLUTION)
    if drain_binding is None:
        return ()
    if not hasattr(agents_policy, "agent_chains"):
        return ()
    chain_config = agents_policy.agent_chains.get(drain_binding.chain)
    if chain_config is None:
        return ()
    agents = tuple(chain_config.agents)
    if len(agents) < _MIN_FALLBACK_CHAIN_LENGTH:
        logger.warning(
            "conflict_resolution: drain '{}' is bound to a one-agent chain '{}'; "
            "there is no fallback candidate if this resolver fails",
            PHASE_RESOLUTION,
            drain_binding.chain,
        )
    return agents


def ensure_session_controller(
    session: ResolutionSession | None,
    policy_bundle: PolicyBundle | None,
    *,
    pipeline_deps: PipelineDeps | None = None,
    config: UnifiedConfig | None = None,
) -> RecoveryController:
    from ralph.agents.timeout_clock import SystemClock
    from ralph.recovery.controller import RecoveryController

    if session is not None and isinstance(session.recovery_controller, RecoveryController):
        return session.recovery_controller
    deps_rc = (
        pipeline_deps.recovery_controller
        if pipeline_deps is not None and hasattr(pipeline_deps, "recovery_controller")
        else None
    )
    if isinstance(deps_rc, RecoveryController):
        controller = deps_rc
        if session is not None:
            session.recovery_controller = controller
            session.ledger = controller.ledger
        return controller
    store = session.unavailability_store if session is not None else None
    ledger = (
        pipeline_deps.ledger
        if pipeline_deps is not None
        and hasattr(pipeline_deps, "ledger")
        and pipeline_deps.ledger is not None
        else (session.ledger if session is not None else None)
    )
    clock = (
        pipeline_deps.recovery_clock
        if pipeline_deps is not None
        and hasattr(pipeline_deps, "recovery_clock")
        and pipeline_deps.recovery_clock is not None
        else SystemClock()
    )
    deps = AttemptRunnerDeps(
        clock=clock,
        invoke=PRODUCTION_INVOKE,
        probe_runner=None,
        workspace_probe=None,
        connectivity=None,
    )
    controller = build_entry_point_recovery_controller(
        AgentEntryPoint.CONFLICT_RESOLUTION,
        config=config,
        policy_bundle=policy_bundle,
        deps=deps,
        ledger=ledger,
        unavailability_store=store,
    )
    if session is not None:
        session.recovery_controller = controller
        session.ledger = controller.ledger
    return controller


def _build_classified_conflict_failure(
    effective_outcome: ConflictAttemptOutcome | None,
    unresolved_count: int,
    agent_name: str,
    raw_failure: BaseException | str | None,
) -> ClassifiedFailure | None:
    from ralph.recovery.classified_failure import ClassifiedFailure
    from ralph.recovery.failure_category import FailureCategory
    from ralph.recovery.unavailability_reason import UnavailabilityReason

    if effective_outcome is None or effective_outcome is ConflictAttemptOutcome.RAW:
        return None
    unavailable_reason: UnavailabilityReason | None = None
    if effective_outcome is ConflictAttemptOutcome.NO_ACTIVITY:
        unavailable_reason = UnavailabilityReason.NO_OUTPUT_AT_START
        reason = "candidate produced no activity before it exited"
    elif effective_outcome in (
        ConflictAttemptOutcome.STALLED,
        ConflictAttemptOutcome.STALLED_NO_PROGRESS,
    ):
        unavailable_reason = UnavailabilityReason.DEAD_AGENT_NO_PROGRESS
        reason = (
            f"agent reported success but made no progress: {unresolved_count} "
            "conflicted path(s) remain unchanged"
        )
    elif effective_outcome is ConflictAttemptOutcome.UNRESOLVED_REMAINING:
        reason = f"agent reported success but {unresolved_count} conflicted path(s) remain"
    else:
        reason = "conflict attempt failed"

    return ClassifiedFailure(
        category=FailureCategory.AGENT,
        reason=reason,
        attributed_agent=agent_name,
        attributed_phase=PHASE_RESOLUTION,
        counts_against_budget=unavailable_reason is not None,
        original_exception=raw_failure if isinstance(raw_failure, BaseException) else None,
        raw_message="" if raw_failure is None else str(raw_failure),
        must_charge_ledger=True,
        is_unavailable=unavailable_reason is not None,
        unavailability_reason=unavailable_reason,
    )


def classify_failed_resolution_attempt(
    session: ResolutionSession | None,
    agent_name: str,
    raw_failure: BaseException | str | None = None,
    *,
    candidates: Sequence[str] = (),
    failed_index: int = 0,
    policy_bundle: PolicyBundle | None = None,
    bench: bool = False,
    workspace_changed: bool = False,
    artifact_valid: bool = False,
    outcome: ConflictAttemptOutcome | None = None,
    unresolved_count: int = 0,
) -> None:
    del bench
    from ralph.pipeline.agent_chain_state import AgentChainState
    from ralph.pipeline.state import PipelineState

    controller = ensure_session_controller(session, policy_bundle)
    effective_outcome = outcome
    if not workspace_changed:
        if outcome is ConflictAttemptOutcome.UNRESOLVED_REMAINING:
            effective_outcome = ConflictAttemptOutcome.STALLED_NO_PROGRESS
        elif outcome is ConflictAttemptOutcome.ATTEMPT_FAILED:
            effective_outcome = (
                ConflictAttemptOutcome.STALLED_NO_PROGRESS
                if session is not None and session.last_attempt_saw_activity
                else ConflictAttemptOutcome.NO_ACTIVITY
            )
    classified = _build_classified_conflict_failure(
        effective_outcome,
        unresolved_count,
        agent_name,
        raw_failure,
    )
    agents = list(candidates) if candidates else [agent_name]
    bounded_index = min(max(failed_index, 0), max(len(agents) - 1, 0))
    if session is None:
        return

    recovery_state = session.recovery_state
    if not isinstance(recovery_state, PipelineState):
        recovery_state = PipelineState(
            phase=PHASE_RESOLUTION,
            phase_chains={
                PHASE_RESOLUTION: AgentChainState(
                    agents=agents, current_index=bounded_index, retries=0
                )
            },
        )
    from ralph.recovery.classifier import FailureContext

    handle_failure: BaseException | str = raw_failure if raw_failure is not None else ""
    facts = session.last_attempt_result.facts if session and session.last_attempt_result else None
    context = FailureContext(
        phase=PHASE_RESOLUTION,
        agent=agent_name,
        classified_failure=classified,
        facts=facts,
    )
    try:
        new_state, _effects, _event = controller.handle(
            recovery_state,
            handle_failure,
            context,
            workspace_changed=workspace_changed,
            artifact_valid=artifact_valid,
            facts=facts,
        )
    except TypeError:
        new_state, _effects, _event = controller.handle(
            recovery_state,
            handle_failure,
            context,
        )
    except Exception as handle_exc:
        logger.debug("controller.handle exception in conflict resolution: {}", handle_exc)
        new_state = recovery_state
    if session is None:
        return
    session.recovery_state = new_state
    session.last_recovery_reason = (
        classified.reason
        if classified is not None
        else (controller.last_attempt_outcome.reason if controller.last_attempt_outcome else None)
    )
    session.last_retry_delay_ms = new_state.last_retry_delay_ms

    agent_status = controller.agent_eligibility(PHASE_RESOLUTION, agent_name)
    agent_available = agent_status.is_eligible

    if not session.skip_same_agent_retries and agent_available:
        session.chain_cursor = failed_index
    else:
        session.skip_same_agent_retries = False
        candidates_tuple = tuple(candidates) if candidates else (agent_name,)
        selection = controller.preferred_agent_index(
            PHASE_RESOLUTION,
            candidates_tuple,
            current_index=failed_index,
            exclude_current=True,
        )
        if selection.agent is not None and selection.index is not None:
            session.chain_cursor = selection.index
        elif candidates:
            session.chain_cursor = controller.next_conflict_candidate(
                candidates, failed_index=failed_index
            )
    chain_state = recovery_state.chain_for_phase(PHASE_RESOLUTION)
    chain_agents: list[str] = (
        list(chain_state.agents) if chain_state is not None else list(candidates)
    )
    session.recovery_state = recovery_state.with_phase_chain(
        PHASE_RESOLUTION,
        AgentChainState(
            agents=chain_agents,
            current_index=min(session.chain_cursor, max(len(chain_agents) - 1, 0)),
            retries=0,
        ),
    )


def conflict_chain_max_retries(policy_bundle: PolicyBundle | None) -> int:
    if policy_bundle is None:
        return 2
    drain = policy_bundle.agents.agent_drains.get(PHASE_RESOLUTION)
    if drain is None:
        return 2
    chain = policy_bundle.agents.agent_chains.get(drain.chain)
    if chain is None:
        return 2
    return chain.max_retries


def invoke_resolution_agent(
    *,
    agent_name: str,
    prompt_path: Path,
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps,
    workspace_scope: WorkspaceScope,
    policy_bundle: PolicyBundle,
    display: ParallelDisplay | None,
    display_context: DisplayContext | None,
    inactivity_timeout_seconds: float | None = None,
    status_interval_seconds: float | None = None,
    activity_status_listener: Callable[[object], None] | None = None,
    unresolved_paths: tuple[str, ...] = (),
    session: ResolutionSession | None = None,
    require_completion_evidence: bool = False,
) -> bool:
    if session is not None:
        session.last_attempt_evidence = None
        session.last_attempt_failure = None
        session.last_attempt_outcome = None
        session.last_attempt_result = None
        session.last_attempt_saw_activity = False
    pop_last_captured_retry_intent()
    if _candidate_cannot_be_launched(agent_name, session):
        return False
    observed_fault: ResolutionTerminationReason | None = None

    def _record_fault(reason: ResolutionTerminationReason) -> None:
        nonlocal observed_fault
        observed_fault = reason

    def _supervision_health_error() -> str | None:
        return None if observed_fault is None else observed_fault.value

    wrapped_listener = wrap_activity_listener(
        activity_status_listener,
        session if session is not None else ResolutionSession(),
        agent_name=agent_name,
        on_fault=_record_fault,
    )
    effect = InvokeAgentEffect(
        agent_name=agent_name,
        phase=PHASE_RESOLUTION,
        prompt_file=str(prompt_path),
        drain=PHASE_RESOLUTION,
        chain_name=PHASE_RESOLUTION,
        requires_completion_evidence=require_completion_evidence,
        activity_only_supervision=True,
        activity_only_status_interval_seconds=status_interval_seconds,
        activity_status_listener=wrapped_listener,
        supervision_health_error=_supervision_health_error,
    )
    controller = ensure_session_controller(
        session,
        policy_bundle,
        pipeline_deps=pipeline_deps,
        config=config,
    )
    try:
        attempt = run_agent_attempt(
            AgentEntryPoint.CONFLICT_RESOLUTION,
            effect=effect,
            config=config,
            controller=controller,
            deps=AttemptRunnerDeps(
                clock=controller.clock,
                invoke=_invoke_effect,
                probe_runner=controller._dead_agent_probe_runner,
                workspace_probe=None,
                connectivity=None,
            ),
            pipeline_deps=pipeline_deps,
            workspace_scope=workspace_scope,
            display=display,
            display_context=display_context,
            policy_bundle=policy_bundle,
            run_id=None,
        )
    except Exception as exc:
        _record_attempt_failure(session, exc)
        _record_resolution_exception(session, exc)
        logger.warning("conflict_resolution: agent '{}' could not be launched: {}", agent_name, exc)
        return False
    if session is not None:
        session.last_attempt_result = attempt
        if attempt.outcome is not None and attempt.outcome.reason:
            session.last_recovery_reason = attempt.outcome.reason
    if attempt.event != PipelineEvent.AGENT_SUCCESS:
        _record_attempt_failure(session, _failed_attempt_evidence(session, agent_name))
        _record_attempt_without_agent_work(session)
        return False
    return True


def _candidate_cannot_be_launched(
    agent_name: str,
    session: ResolutionSession | None,
) -> bool:
    if session is not None:
        controller = session.recovery_controller
        ledger = session_ledger(session)
        is_available = True
        if controller is not None:
            is_available = controller.agent_eligibility(PHASE_RESOLUTION, agent_name).is_eligible
        elif ledger is not None:
            is_available = ledger.agent_eligibility(PHASE_RESOLUTION, agent_name).is_eligible
        if not is_available:
            logger.warning(
                "conflict_resolution: refusing to launch unavailable agent '{}'",
                agent_name,
            )
            session.terminal_reason = ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
            session.last_failure_cause = ResolutionFailureCause.NONE
            session.charge_conflict_budget = False
            return True
        if agent_name in session.dead_tool_surfaces:
            logger.warning(
                "conflict_resolution: refusing to re-enter known-dead tool surface '{}'",
                agent_name,
            )
            session.terminal_reason = ResolutionTerminationReason.TOOL_SURFACE_DEAD
            session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
            session.charge_conflict_budget = False
            return True
    return False


def _record_attempt_without_agent_work(session: ResolutionSession | None) -> None:
    if session is None or session.last_attempt_saw_activity:
        return
    session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
    if session.terminal_reason is None:
        session.terminal_reason = ResolutionTerminationReason.CANDIDATE_EXITED
    session.last_attempt_outcome = ConflictAttemptOutcome.NO_ACTIVITY


def _record_attempt_failure(
    session: ResolutionSession | None, failure: BaseException | str
) -> None:
    if session is not None:
        session.last_attempt_failure = failure


def _failed_attempt_evidence(session: ResolutionSession | None, agent_name: str) -> str:
    intent = pop_last_captured_retry_intent()
    if session is not None and intent.skip_same_agent_retries:
        session.skip_same_agent_retries = True
    if session is not None:
        session.last_attempt_outcome = ConflictAttemptOutcome.ATTEMPT_FAILED
    if not intent.failure_reason:
        return ATTEMPT_FAILED_EVIDENCE
    if session is not None:
        session.last_attempt_evidence = intent.failure_reason
        if intent.failure_reason == AGENT_NOT_FOUND_REASON:
            session.terminal_reason = ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
            logger.warning(
                "conflict_resolution: candidate '{}' is not installed in this "
                "workspace; handing over to the next candidate",
                agent_name,
            )
    return intent.failure_reason


def _record_resolution_termination(session: ResolutionSession | None, exc: Exception) -> None:
    if session is None:
        return
    session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
    if isinstance(exc, AgentInactivityTimeoutError):
        diagnostic = exc.diagnostic
        reason = exc.reason
        session.terminal_reason = (
            ResolutionTerminationReason.OPERATOR_CAP_REACHED
            if reason == WatchdogFireReason.OPERATOR_CAP_REACHED
            else ResolutionTerminationReason.CONFLICT_INACTIVITY
        )
        raw_kind = diagnostic.get("last_activity_kind")
        session.last_activity_kind = raw_kind if isinstance(raw_kind, str) else None
        raw_at = diagnostic.get("last_activity_at")
        session.last_activity_at = float(raw_at) if isinstance(raw_at, (int, float)) else None
        raw_duration = diagnostic.get("invocation_elapsed_seconds")
        session.last_duration_seconds = (
            float(raw_duration) if isinstance(raw_duration, (int, float)) else exc.timeout_seconds
        )
        return
    session.terminal_reason = (
        classify_ralph_origin_fault(exc.detail)
        if isinstance(exc, SupervisionInfrastructureError)
        else None
    ) or ResolutionTerminationReason.SUPERVISION_INFRASTRUCTURE_FAILURE
    session.charge_conflict_budget = False


def _record_resolution_exception(
    session: ResolutionSession | None, exc: Exception | None = None
) -> None:
    if session is not None:
        from ralph.agents.invoke._supervision_infrastructure_error import (
            SupervisionInfrastructureError,
        )

        if isinstance(exc, SupervisionInfrastructureError):
            session.charge_conflict_budget = False
            if session.terminal_reason is None:
                session.terminal_reason = (
                    ResolutionTerminationReason.SUPERVISION_INFRASTRUCTURE_FAILURE
                )
        elif session.terminal_reason is None:
            session.terminal_reason = ResolutionTerminationReason.EXCEPTION
        session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE


def _log_resolution_termination(exc: Exception, unresolved_paths: tuple[str, ...]) -> None:
    fields: dict[str, str | int | float | bool | list[object]]
    if isinstance(exc, AgentInactivityTimeoutError):
        reason_value = (
            exc.reason.value
            if isinstance(exc.reason, WatchdogFireReason)
            else "CONFLICT_INACTIVITY"
        )
        fields = exc.diagnostic
    else:
        observed = (
            classify_ralph_origin_fault(exc.detail)
            if isinstance(exc, SupervisionInfrastructureError)
            else None
        )
        reason_value = (
            observed or ResolutionTerminationReason.SUPERVISION_INFRASTRUCTURE_FAILURE
        ).value
        fields = {}
    logger.warning(
        "conflict_resolution termination: reason={}; last_activity_kind={}; "
        "last_activity_at={}; duration_seconds={}; unresolved_paths={}",
        reason_value,
        fields.get("last_activity_kind", "none"),
        fields.get("last_activity_at", "never"),
        fields.get("invocation_elapsed_seconds", "unknown"),
        ", ".join(unresolved_paths),
    )


_RALPH_AUTHORED_DIAGNOSTIC_KEYS = (
    "last_activity_kind",
    "last_fire_reason",
    "last_deferred_kind",
)


def _ralph_authored_fault_text(event: object) -> str:
    diagnostic: object = getattr(event, "diagnostic", None)
    if not isinstance(diagnostic, dict):
        return ""
    authored: list[str] = []
    for key in _RALPH_AUTHORED_DIAGNOSTIC_KEYS:
        value: object = diagnostic.get(key)
        if isinstance(value, str):
            authored.append(value)
    return " ".join(authored)


def _observed_ralph_fault(
    event: object, session: ResolutionSession | None
) -> ResolutionTerminationReason | None:
    del session
    observed = classify_ralph_origin_fault(_ralph_authored_fault_text(event))
    if observed is not None:
        logger.warning(
            "conflict_resolution: '{}' observed in Ralph diagnostic event",
            observed.value,
        )
        return observed
    raw_fault = classify_ralph_origin_fault(str(event))
    if raw_fault is not None:
        logger.info(
            "conflict_resolution: fault pattern '{}' observed in agent activity text; "
            "retaining for diagnostic reporting",
            raw_fault.value,
        )
    return None


def wrap_activity_listener(
    listener: Callable[[object], None] | None,
    session: ResolutionSession | None,
    *,
    agent_name: str,
    on_fault: Callable[[ResolutionTerminationReason], None] | None = None,
) -> Callable[[object], None] | None:

    def _observe(event: object) -> None:
        if session is not None:
            session.last_attempt_saw_activity = True
        reason = _observed_ralph_fault(event, session)
        if reason is not None and session is not None:
            session.terminal_reason = reason
            session.charge_conflict_budget = False
            if on_fault is not None:
                on_fault(reason)
            return
        if listener is not None:
            listener(event)

    return _observe
