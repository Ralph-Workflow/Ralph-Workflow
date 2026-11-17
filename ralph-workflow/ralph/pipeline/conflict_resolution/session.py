from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    SupervisionInfrastructureError,
)
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline import effect_executor as _effect_executor_module
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.agent_entry_point import (
    AgentEntryPoint,
    register_entry_point,
    registered_entry_points,
)
from ralph.pipeline.attempt_result import AttemptResult
from ralph.pipeline.attempt_runner import run_agent_attempt
from ralph.pipeline.attempt_runner_deps import (
    AttemptRunnerDeps,
    _as_effect_runner,
    effect_attempt_invoke,
)
from ralph.pipeline.conflict_resolution._errors import RecoveryControllerUnavailableError
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.attempt_fault import (
    classify_ralph_origin_fault,
)
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.effect_executor import (
    AGENT_NOT_FOUND_REASON,
    pop_last_captured_retry_intent,
)
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.resolution_outcome import ResolvedOutcome, RetryLaterOutcome
from ralph.recovery.agent_failure_ledger import AgentFailureLedger
from ralph.recovery.attempt_facts import AttemptFacts
from ralph.recovery.attribution import _NO_RULE_FIRED_REASON
from ralph.recovery.conflict_attempt_outcome import ConflictAttemptOutcome
from ralph.recovery.controller import RecoveryController
from ralph.recovery.failure_classifier import typed_outcome_reason

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from ralph.config.gitlink_policy_config import GitlinkPolicyConfig
    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.factory import PipelineDeps
    from ralph.pipeline.resolution_outcome import StopResolutionOutcome
    from ralph.policy.models import PolicyBundle
    from ralph.recovery.agent_unavailability_tracker import UnavailabilityStore
    from ralph.recovery.canary_probe import ProbeOutcome
    from ralph.workspace.probe import WorkspaceChangeProbe
    from ralph.workspace.protocol import Workspace
    from ralph.workspace.scope import WorkspaceScope

__all__ = [
    "ATTEMPT_FAILED_EVIDENCE",
    "RecoveryControllerUnavailableError",
    "ResolutionSession",
    "ResolutionTerminationReason",
    "begin_resolution_stop",
    "ensure_session_controller",
    "finalize_resolution_attempt_failure",
    "invoke_resolution_agent",
    "remember_dead_surface",
    "resolution_chain_agents",
    "session_ledger",
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

ATTEMPT_FAILED_EVIDENCE = "conflict attempt failed"


@dataclass
class ResolutionSession:
    started_at: float | None = None
    unresolved_paths: tuple[str, ...] = ()
    initial_conflicted_count: int | None = None
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
    out_of_reach_paths: tuple[str, ...] = ()
    last_recovery_reason: str | None = None
    recovery_controller: RecoveryController | None = None
    unavailability_store: UnavailabilityStore | None = None
    ledger: AgentFailureLedger | None = None
    recovery_state: object | None = None
    exhaustion_reason: str | None = None
    operator_action_message: str | None = None
    last_attempt_evidence: str | None = None
    last_attempt_failure: BaseException | str | None = None
    last_attempt_outcome: ConflictAttemptOutcome | None = None
    last_attempt_result: AttemptResult | None = None
    last_attempt_saw_activity: bool = False
    rounds_spent: int = 0
    last_failure_cause: ResolutionFailureCause = ResolutionFailureCause.NONE
    workspace: Workspace | None = None
    workspace_probe: WorkspaceChangeProbe | None = None
    probe_runner: Callable[[str, str], ProbeOutcome | None] | None = None
    caller_provided: bool = False
    last_resolution_outcome: StopResolutionOutcome | None = None
    inactivity_timeout_seconds: float | None = None


def begin_resolution_stop(
    session: ResolutionSession,
    *,
    is_strategy_rotation: bool = False,
) -> None:
    from ralph.pipeline.state import PipelineState

    session.out_of_reach_paths = ()
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
    return tuple(chain_config.agents)


def _bind_session_controller(
    session: ResolutionSession | None, controller: RecoveryController
) -> RecoveryController:
    if session is not None:
        session.recovery_controller = controller
        session.ledger = controller.ledger
        session.unavailability_store = controller.unavailability_store
    return controller


def _require_bound_session_controller(session: ResolutionSession | None) -> RecoveryController:
    if session is not None and isinstance(session.recovery_controller, RecoveryController):
        return session.recovery_controller
    raise RecoveryControllerUnavailableError(
        "conflict resolution requires the run's shared RecoveryController; none is bound"
    )


def ensure_session_controller(
    session: ResolutionSession | None,
    policy_bundle: PolicyBundle | None = None,
    *,
    pipeline_deps: PipelineDeps | None = None,
    config: UnifiedConfig | None = None,
) -> RecoveryController:
    from ralph.pipeline._integration_recovery_controller import (
        reachable_shared_recovery_controller,
    )

    del policy_bundle, config
    rc_candidate = pipeline_deps.recovery_controller if pipeline_deps is not None else None
    if isinstance(rc_candidate, RecoveryController):
        return _bind_session_controller(session, rc_candidate)
    session_controller = (
        session.recovery_controller
        if session is not None and isinstance(session.recovery_controller, RecoveryController)
        else None
    )
    ledger = (
        session.ledger
        if session is not None and session.ledger is not None
        else (pipeline_deps.ledger if pipeline_deps is not None else None)
    )
    shared = reachable_shared_recovery_controller(
        explicit=session_controller,
        ledger=ledger,
    )
    if shared is not None:
        return _bind_session_controller(session, shared)
    raise RecoveryControllerUnavailableError(
        "conflict resolution requires the run's shared RecoveryController; none is reachable"
    )


def finalize_resolution_attempt_failure(
    session: ResolutionSession | None,
    agent_name: str,
    *,
    facts: AttemptFacts,
    exc: BaseException | None = None,
    outcome: ConflictAttemptOutcome | None = None,
    policy_bundle: PolicyBundle | None = None,
    candidates: Sequence[str] = (),
    failed_index: int = 0,
) -> None:
    from ralph.pipeline.state import PipelineState
    from ralph.recovery.classifier import FailureContext

    del policy_bundle
    if session is None:
        return
    controller = _require_bound_session_controller(session)
    agents = list(candidates) if candidates else [agent_name]
    bounded_index = min(max(failed_index, 0), max(len(agents) - 1, 0))
    recovery_state = (
        session.recovery_state
        if isinstance(session.recovery_state, PipelineState)
        else PipelineState(
            phase=PHASE_RESOLUTION,
            phase_chains={
                PHASE_RESOLUTION: AgentChainState(
                    agents=agents,
                    current_index=bounded_index,
                    retries=0,
                )
            },
        )
    )
    typed_outcome = (
        outcome if outcome is not None and outcome is not ConflictAttemptOutcome.RAW else None
    )
    unresolved_count = facts.unresolved_count or 0
    typed_reason = (
        typed_outcome_reason(typed_outcome, unresolved_count) if typed_outcome is not None else None
    )
    if typed_outcome is not None:
        logger.info(
            "conflict_resolution: typed attempt outcome '{}' for agent '{}' in phase '{}': {}",
            typed_outcome.value,
            agent_name,
            PHASE_RESOLUTION,
            typed_reason,
        )
    raw_failure: BaseException | str = exc if exc is not None else (typed_reason or "")
    new_state, _effects, _failure_event = controller.handle(
        recovery_state,
        raw_failure,
        FailureContext(
            phase=PHASE_RESOLUTION,
            agent=agent_name,
            facts=facts,
            outcome=typed_outcome,
            unresolved_count=unresolved_count,
        ),
        workspace_changed=bool(facts.workspace_changed),
        artifact_valid=bool(facts.artifact_valid),
        facts=facts,
    )
    session.recovery_state = new_state
    attempt_outcome = controller.last_attempt_outcome
    if attempt_outcome is not None:
        session.last_attempt_result = AttemptResult(
            PipelineEvent.AGENT_FAILURE,
            facts,
            attempt_outcome,
            ((1, attempt_outcome),),
        )
        evidence = typed_reason or attempt_outcome.reason
        if evidence:
            session.last_recovery_reason = evidence
            session.last_attempt_evidence = evidence
    if exc is not None:
        session.last_attempt_failure = exc
    candidates_tuple = tuple(agents)
    selection = controller.preferred_agent_index(
        PHASE_RESOLUTION,
        candidates_tuple,
        current_index=failed_index,
        exclude_current=True,
    )
    if selection.agent is not None and selection.index is not None:
        next_cursor = failed_index if selection.index < failed_index else selection.index
    else:
        next_cursor = failed_index
    session.chain_cursor = next_cursor
    chain_state = new_state.chain_for_phase(PHASE_RESOLUTION)
    chain_agents = list(chain_state.agents) if chain_state is not None else list(candidates_tuple)
    same_candidate = chain_state is not None and chain_state.current_index == next_cursor
    next_retries = chain_state.retries if (chain_state is not None and same_candidate) else 0
    session.recovery_state = new_state.with_phase_chain(
        PHASE_RESOLUTION,
        AgentChainState(
            agents=chain_agents,
            current_index=min(next_cursor, max(len(chain_agents) - 1, 0)),
            retries=next_retries,
        ),
    )


def _record_session_failure_result(
    session: ResolutionSession | None,
    controller: RecoveryController,
    agent_name: str,
    exc: BaseException,
) -> None:
    if session is None:
        return
    facts = AttemptFacts(artifact_valid=False, workspace_changed=False)
    outcome = controller.last_attempt_outcome
    if outcome is None:
        outcome = controller.finalize_attempt(
            PHASE_RESOLUTION,
            agent_name,
            facts=facts,
            attempt_failed=True,
            exc=exc,
        )
    session.last_attempt_result = AttemptResult(
        event=PipelineEvent.AGENT_FAILURE,
        facts=facts,
        outcome=outcome,
        attempt_outcomes=((1, outcome),),
    )


def invoke_resolution_agent(
    *,
    agent_name: str,
    prompt_path: Path,
    config: UnifiedConfig,
    pipeline_deps: PipelineDeps | None = None,
    workspace_scope: WorkspaceScope | None,
    policy_bundle: PolicyBundle,
    display: ParallelDisplay | None,
    display_context: DisplayContext | None,
    unresolved_paths: tuple[str, ...] = (),
    session: ResolutionSession | None = None,
    require_completion_evidence: bool = False,
) -> StopResolutionOutcome:
    if session is not None:
        session.last_attempt_evidence = None
        session.last_attempt_failure = None
        session.last_attempt_outcome = None
        session.last_attempt_result = None
        session.last_attempt_saw_activity = False
    if display_context is None:
        from ralph.display.context import make_display_context

        display_context = make_display_context()
    caller_deps = pipeline_deps
    if caller_deps is None:
        caller_deps = _effect_executor_module.current_agent_effect_dependencies().pipeline_deps
    effective_deps = caller_deps
    if effective_deps is None:
        from ralph.pipeline.factory import DefaultPipelineFactory

        effective_deps = DefaultPipelineFactory().build(config, display_context)
    if workspace_scope is None:
        from ralph.workspace.scope import WorkspaceScope

        workspace_scope = WorkspaceScope(prompt_path.parent)
    pop_last_captured_retry_intent()
    controller = ensure_session_controller(
        session,
        policy_bundle,
        pipeline_deps=caller_deps,
        config=config,
    )
    if effective_deps.recovery_controller is not controller:
        import dataclasses

        effective_deps = dataclasses.replace(effective_deps, recovery_controller=controller)
    activity_only = not require_completion_evidence
    effect = InvokeAgentEffect(
        agent_name=agent_name,
        phase=PHASE_RESOLUTION,
        prompt_file=str(prompt_path),
        drain=PHASE_RESOLUTION,
        chain_name=PHASE_RESOLUTION,
        requires_completion_evidence=require_completion_evidence,
        activity_only_supervision=activity_only,
        activity_only_status_interval_seconds=(
            config.conflict_resolution.status_interval_seconds if activity_only else None
        ),
    )
    try:
        attempt = run_agent_attempt(
            AgentEntryPoint.CONFLICT_RESOLUTION,
            effect=effect,
            config=config,
            controller=controller,
            deps=AttemptRunnerDeps(
                clock=controller.clock,
                invoke=effect_attempt_invoke(
                    _as_effect_runner(_effect_executor_module.execute_agent_effect)
                ),
                probe_runner=None,
                workspace_probe=None,
                connectivity=None,
            ),
            pipeline_deps=effective_deps,
            workspace_scope=workspace_scope,
            display=display,
            display_context=display_context,
            policy_bundle=policy_bundle,
            run_id=None,
        )
    except (AgentInvocationError, OSError, RuntimeError, ValueError, KeyError) as exc:
        _record_attempt_failure(session, exc)
        if isinstance(exc, (AgentInactivityTimeoutError, SupervisionInfrastructureError)):
            _record_resolution_termination(session, exc)
            _log_resolution_termination(exc, unresolved_paths)
        else:
            _record_resolution_exception(session)
            logger.warning(
                "conflict_resolution: agent '{}' could not be launched: {}", agent_name, exc
            )
        _record_session_failure_result(session, controller, agent_name, exc)
        return _failed_invocation_outcome(
            exc,
            session,
            unresolved_paths,
        )
    if session is not None:
        session.last_attempt_result = attempt
        if attempt.outcome is not None and attempt.outcome.reason:
            session.last_recovery_reason = attempt.outcome.reason
            if attempt.outcome.reason != _NO_RULE_FIRED_REASON:
                session.last_attempt_evidence = attempt.outcome.reason
                session.last_attempt_failure = attempt.outcome.reason
    if attempt.event != PipelineEvent.AGENT_SUCCESS:
        failure_evidence = _failed_attempt_evidence(session, agent_name)
        _record_attempt_failure(session, failure_evidence)
        _record_attempt_without_agent_work(session)
        return _failed_invocation_outcome(
            RuntimeError(failure_evidence),
            session,
            (),
        )
    return ResolvedOutcome(
        terminal_reason=session.terminal_reason if session is not None else None,
        unresolved_paths=session.unresolved_paths if session is not None else (),
    )


def _failed_invocation_outcome(
    error: Exception,
    session: ResolutionSession | None,
    unresolved_paths: tuple[str, ...],
) -> RetryLaterOutcome:
    del error
    return RetryLaterOutcome(
        on_change="resolver_agent_eligibility_changed",
        terminal_reason=session.terminal_reason if session is not None else None,
        unresolved_paths=session.unresolved_paths if session is not None else unresolved_paths,
    )


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
    if session is not None:
        session.last_attempt_outcome = ConflictAttemptOutcome.ATTEMPT_FAILED
    if not intent.failure_reason or (
        intent.failed_agent_name is not None and intent.failed_agent_name != agent_name
    ):
        return (
            session.last_attempt_evidence
            if session is not None and session.last_attempt_evidence
            else ATTEMPT_FAILED_EVIDENCE
        )
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


def _record_resolution_exception(session: ResolutionSession | None) -> None:
    if session is not None:
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
    try:
        diagnostic: object = object.__getattribute__(event, "diagnostic")
    except AttributeError:
        diagnostic = None
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


def session_ledger(session: ResolutionSession | None) -> AgentFailureLedger | None:
    if session is None:
        return None
    ledger = session.ledger
    if isinstance(ledger, AgentFailureLedger):
        return ledger
    controller = session.recovery_controller
    if controller is None:
        return None
    controller_ledger: object = controller.ledger
    if isinstance(controller_ledger, AgentFailureLedger):
        return controller_ledger
    return None


def remember_dead_surface(session: ResolutionSession, agent_name: str) -> None:
    if session.terminal_reason is not ResolutionTerminationReason.CANDIDATE_UNAVAILABLE:
        return
    controller = _require_bound_session_controller(session)
    controller.mark_candidate_unavailable(PHASE_RESOLUTION, agent_name)
