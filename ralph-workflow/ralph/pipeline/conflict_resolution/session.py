"""Run one conflict-resolution attempt through the normal MCP invocation seam."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke import AgentInactivityTimeoutError, SupervisionInfrastructureError
from ralph.pipeline import effect_executor as _effect_executor_module
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.attempt_fault import (
    classify_ralph_origin_fault,
)
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.rebase_state import ResolutionFailureCause

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.factory import PipelineDeps
    from ralph.policy.models import PolicyBundle
    from ralph.recovery.agent_failure_ledger import AgentFailureLedger
    from ralph.recovery.agent_unavailability_tracker import UnavailabilityStore
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

_MIN_FALLBACK_CHAIN_LENGTH = 2

#: Evidence text for an invocation that ran and came back unsuccessful
#: with nothing more specific to say. It is not a refusal: the agent has
#: no way to refuse, so this is a FAILED attempt and the chain answers it
#: by trying again.
ATTEMPT_FAILED_EVIDENCE = "conflict attempt failed"


@dataclass
class ResolutionSession:
    """Timing and unresolved-path context spanning one complete resolution."""

    started_at: float | None = None
    unresolved_paths: tuple[str, ...] = ()
    inactivity_timeout_seconds: float | None = None
    max_rounds_per_stop: int | None = None
    max_rebase_conflict_stops: int | None = None
    max_fallback_agents: int | None = None
    total_resolution_cap_seconds: float | None = None
    terminal_reason: ResolutionTerminationReason | None = None
    last_activity_kind: str | None = None
    last_activity_at: float | None = None
    last_duration_seconds: float | None = None
    chain_cursor: int = 0
    charge_conflict_budget: bool = True
    #: Candidates this workspace cannot produce at all. Deterministic, so
    #: it holds for the whole rebase: the name will not appear mid-run.
    dead_tool_surfaces: tuple[str, ...] = ()
    #: Candidates whose TOOL SURFACE faulted -- a transport loop, an
    #: unanswered supervision relay. That is Ralph's own plumbing and the
    #: recovery layer calls it retryable, so it is scoped to the stop it
    #: happened on rather than killing the agent for the whole rebase.
    #: Paths of this stop that no resolver can repair. The rest are still
    #: resolved; these are escalated afterwards, by name.
    out_of_reach_paths: tuple[str, ...] = ()
    last_recovery_reason: str | None = None
    recovery_controller: RecoveryController | None = None
    #: The run's shared cooldown ledger, adopted from ``PipelineDeps`` when the
    #: driver starts. The session's own controller is built on top of it.
    unavailability_store: UnavailabilityStore | None = None
    ledger: AgentFailureLedger | None = None
    recovery_state: object | None = None
    last_retry_delay_ms: int = 0
    exhaustion_reason: str | None = None
    skip_same_agent_retries: bool = False
    last_attempt_evidence: str | None = None
    last_attempt_failure: BaseException | str | None = None
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
    """Reset the state that describes ONE conflict before resolving another.

    A session spans a whole rebase, so everything here would otherwise
    arrive at the next stop still describing the last one: a chain
    cursor parked past the end starves the new stop of every candidate,
    and a terminal reason left over from the previous conflict is
    reported as this conflict's verdict even when no resolver ran.

    When ``is_strategy_rotation`` is True, the session is retrying the
    SAME conflict under a new strategy; retry budgets, spent rounds and
    dead tool surfaces must not be replenished.

    ``dead_tool_surfaces`` deliberately survives -- a tool surface that
    died is still dead -- and so does the RecoveryController itself,
    with the budgets and cooldowns that are meant to span the rebase.
    What is reset is the POSITION: this stop starts at the head of the
    chain, and the controller's chain state is re-synced to say so, so a
    candidate it had already stepped past earlier in the rebase is
    offered again for a conflict it has not seen.
    """
    from ralph.pipeline.agent_chain_state import AgentChainState
    from ralph.pipeline.state import PipelineState

    session.out_of_reach_paths = ()
    session.skip_same_agent_retries = False
    session.last_attempt_evidence = None
    session.last_attempt_failure = None
    session.last_attempt_saw_activity = False
    session.terminal_reason = None
    session.last_activity_kind = None
    session.last_activity_at = None
    session.last_duration_seconds = None
    session.exhaustion_reason = None
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
    """Return the configured conflict-resolution candidate chain."""
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
) -> RecoveryController:
    """Return the session's recovery controller, building it on the shared ledger.

    The controller's retry budgets are per session, and its failure ledger is
    partitioned by phase (``PHASE_RESOLUTION``) to maintain strict phase
    isolation.
    """
    from ralph.recovery.agent_failure_ledger import AgentFailureLedger
    from ralph.recovery.controller import RecoveryController
    from ralph.recovery.recovery_controller_options import RecoveryControllerOptions
    from ralph.recovery.seed_budget_registry import seed_budget_registry

    if session is not None and isinstance(session.recovery_controller, RecoveryController):
        return session.recovery_controller
    if session is not None and session.ledger is None:
        if pipeline_deps is not None and pipeline_deps.ledger is not None:
            session.ledger = pipeline_deps.ledger
        if session.ledger is None and session.unavailability_store is not None:
            store_ledger: object = getattr(session.unavailability_store, "_failure_ledger", None)
            if isinstance(store_ledger, AgentFailureLedger):
                session.ledger = store_ledger
            else:
                clock_inst: object = getattr(session.unavailability_store, "_clock", None)
                from ralph.agents.clock import Clock

                if isinstance(clock_inst, Clock):
                    new_ledger = AgentFailureLedger(clock=clock_inst)
                    session.ledger = new_ledger
                    object.__setattr__(session.unavailability_store, "_failure_ledger", new_ledger)
    ledger_instance = (
        session.ledger
        if session is not None and isinstance(session.ledger, AgentFailureLedger)
        else None
    )
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            policy_bundle=policy_bundle,
            budget_registry=(
                seed_budget_registry(policy_bundle) if policy_bundle is not None else None
            ),
            unavailability_store=session.unavailability_store if session is not None else None,
            ledger=ledger_instance,
        )
    )
    if session is not None:
        session.recovery_controller = controller
        if session.ledger is None:
            session.ledger = controller.ledger
    return controller


def classify_failed_resolution_attempt(
    session: ResolutionSession | None,
    agent_name: str,
    raw_failure: BaseException | str,
    *,
    candidates: Sequence[str] = (),
    failed_index: int = 0,
    policy_bundle: PolicyBundle | None = None,
    bench: bool = False,
    workspace_changed: bool = False,
    artifact_valid: bool = False,
) -> None:
    """Route a failed conflict invoke through RecoveryController.handle."""
    del bench
    from ralph.pipeline.agent_chain_state import AgentChainState
    from ralph.pipeline.state import PipelineState
    from ralph.recovery.classifier import FailureContext

    controller = ensure_session_controller(session, policy_bundle)
    classified = controller.classify_conflict_attempt(raw_failure, agent=agent_name)
    agents = list(candidates) if candidates else [agent_name]
    bounded_index = min(max(failed_index, 0), max(len(agents) - 1, 0))
    recovery_state: PipelineState
    if session is not None and isinstance(session.recovery_state, PipelineState):
        recovery_state = session.recovery_state
    else:
        recovery_state = PipelineState(
            phase=PHASE_RESOLUTION,
            phase_chains={
                PHASE_RESOLUTION: AgentChainState(
                    agents=agents,
                    current_index=bounded_index,
                    retries=0,
                )
            },
        )
    try:
        new_state, _effects, _event = controller.handle(
            recovery_state,
            raw_failure,
            FailureContext(
                phase=PHASE_RESOLUTION,
                agent=agent_name,
                classified_failure=classified,
            ),
            workspace_changed=workspace_changed,
            artifact_valid=artifact_valid,
        )
    except TypeError:
        new_state, _effects, _event = controller.handle(
            recovery_state,
            raw_failure,
            FailureContext(
                phase=PHASE_RESOLUTION,
                agent=agent_name,
                classified_failure=classified,
            ),
        )
    if session is None:
        return
    session.recovery_state = new_state
    session.last_recovery_reason = classified.reason
    session.last_retry_delay_ms = new_state.last_retry_delay_ms

    agent_available = False
    if hasattr(controller, "ledger") and controller.ledger is not None:
        agent_available = controller.ledger.is_available(PHASE_RESOLUTION, agent_name)

    if not session.skip_same_agent_retries and agent_available:
        session.chain_cursor = failed_index
    else:
        session.skip_same_agent_retries = False
        if candidates:
            session.chain_cursor = controller.next_conflict_candidate(
                candidates, failed_index=failed_index
            )
    chain_state = new_state.chain_for_phase(PHASE_RESOLUTION)
    chain_agents: list[str] = (
        list(chain_state.agents) if chain_state is not None else list(candidates)
    )
    session.recovery_state = new_state.with_phase_chain(
        PHASE_RESOLUTION,
        AgentChainState(
            agents=chain_agents,
            current_index=min(session.chain_cursor, max(len(chain_agents) - 1, 0)),
            retries=0,
        ),
    )


def conflict_chain_max_retries(policy_bundle: PolicyBundle | None) -> int:
    """Return the bound conflict chain's max_retries, or 2 when unbound."""
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
    operator_cap_seconds: float | None = None,
    inactivity_timeout_seconds: float | None = None,
    status_interval_seconds: float | None = None,
    activity_status_listener: Callable[[object], None] | None = None,
    unresolved_paths: tuple[str, ...] = (),
    session: ResolutionSession | None = None,
    require_completion_evidence: bool = False,
) -> bool:
    """Run one activity-only conflict attempt using the chain's retry budget.

    ``require_completion_evidence`` is set when the stop contains a
    conflict whose resolution cannot be seen in the file: a modify/delete
    carries no markers, so the usual textual proof is satisfied before
    anyone touches it and only the resolver saying it decided can tell a
    decision from an untouched file.
    """
    if session is not None:
        session.last_attempt_evidence = None
        session.last_attempt_failure = None
        session.last_attempt_saw_activity = False
    # The retry intent is a thread-local the executor writes on failure
    # and clears only on SUCCESS, so a candidate that failed through one
    # of the exception paths below parks its intent there. Discarding it
    # on entry is what stops the NEXT candidate from being reported with
    # the last one's exit reason -- and from inheriting its
    # ``skip_same_agent_retries``, which would spend that healthy
    # candidate's whole retry budget on somebody else's failure.
    _effect_executor_module.pop_last_captured_retry_intent()
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
        activity_only_operator_cap_seconds=operator_cap_seconds,
        activity_only_status_interval_seconds=status_interval_seconds,
        activity_status_listener=wrapped_listener,
        supervision_health_error=_supervision_health_error,
    )
    conflict_limits = config.conflict_resolution.model_copy(
        update={
            "inactivity_timeout_seconds": (
                inactivity_timeout_seconds
                if inactivity_timeout_seconds is not None
                else config.conflict_resolution.inactivity_timeout_seconds
            )
        }
    )
    conflict_config = config.model_copy(
        update={
            "conflict_resolution": conflict_limits,
        }
    )
    try:
        event = _effect_executor_module.execute_agent_effect(
            effect,
            conflict_config,
            pipeline_deps,
            workspace_scope,
            display=display,
            display_context=display_context,
            policy_bundle=policy_bundle,
            run_id=None,
        )
    except AgentInactivityTimeoutError as exc:
        _record_attempt_failure(session, exc)
        _record_resolution_termination(session, exc)
        _log_resolution_termination(exc, unresolved_paths)
        return False
    except SupervisionInfrastructureError as exc:
        _record_attempt_failure(session, exc)
        _record_resolution_termination(session, exc)
        _log_resolution_termination(exc, unresolved_paths)
        return False
    except Exception as exc:
        _record_attempt_failure(session, exc)
        _record_resolution_exception(session)
        logger.warning("conflict_resolution: agent '{}' could not be launched: {}", agent_name, exc)
        return False
    if event != PipelineEvent.AGENT_SUCCESS:
        _record_attempt_failure(session, _failed_attempt_evidence(session, agent_name))
        _record_attempt_without_agent_work(session)
        return False
    return True


def _candidate_cannot_be_launched(
    agent_name: str,
    session: ResolutionSession | None,
) -> bool:
    """Whether this candidate must not be launched, with the reason recorded.

    Ralph's own refusal, decided before any agent runs, so it must not
    reach the driver as a resolver's verdict.
    """
    if session is not None:
        if session.ledger is not None and not session.ledger.is_available(
            PHASE_RESOLUTION, agent_name
        ):
            logger.warning(
                "conflict_resolution: refusing to launch unavailable agent '{}'",
                agent_name,
            )
            session.terminal_reason = ResolutionTerminationReason.TOOL_SURFACE_DEAD
            session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
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
    """Refuse to call it a decline when the agent never did anything.

    ``execute_agent_effect`` answers with a non-success EVENT for
    failures that happen BEFORE any agent runs -- a name the registry
    cannot produce, a dispatch the policy refuses, a session bridge that
    will not build. Those came back indistinguishable from a resolver
    that had run: "invoking X", then X apparently reading the conflict
    and giving up, in the time it takes to look up a dict.

    The signal that separates them is the supervision stream: while an
    agent session is up, the watchdog ticks activity events through this
    invocation's listener. A tick is not proof the agent did useful
    work, but the COMPLETE absence of one means the invocation never
    reached a supervised session at all, so there was nothing there to
    answer with. Such a round reports an exit and the chain moves on to
    the next candidate.
    """
    if session is None or session.last_attempt_saw_activity:
        return
    session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE
    if session.terminal_reason is None:
        session.terminal_reason = ResolutionTerminationReason.CANDIDATE_EXITED
    if not session.last_attempt_evidence:
        session.last_attempt_evidence = "candidate produced no activity before it exited"


def _record_attempt_failure(
    session: ResolutionSession | None, failure: BaseException | str
) -> None:
    """Hand the real failure to the caller that knows the chain.

    Classifying here as WELL as in the driver ran every failure through
    RecoveryController twice, and the second verdict was decided against
    a session the first had already charged: a transient provider error
    spent its whole same-agent retry budget on one attempt and failed
    over immediately, which is the opposite of what the budget is for.
    The driver classifies once, and it is the one that knows which
    candidate index failed out of which chain.
    """
    if session is not None:
        session.last_attempt_failure = failure


def _failed_attempt_evidence(session: ResolutionSession | None, agent_name: str) -> str:
    """Say what actually failed rather than blaming the resolver for it.

    ``execute_agent_effect`` swallows a terminal invocation error and
    hands back a non-success EVENT, parking the real cause in the
    captured retry intent: pi exiting on an exhausted context or a dead
    provider, a broken agent, missing credentials. Reading it here is
    the difference between "the provider is down" and "the attempt ran
    and failed" -- and only the second is ``ATTEMPT_FAILED``. The
    intent is popped rather than peeked so a
    conflict attempt cannot leave its verdict behind for the next phase
    to inherit.
    """
    intent = _effect_executor_module.pop_last_captured_retry_intent()
    if session is not None and intent.skip_same_agent_retries:
        session.skip_same_agent_retries = True
    if not intent.failure_reason:
        return ATTEMPT_FAILED_EVIDENCE
    if session is not None:
        session.last_attempt_evidence = intent.failure_reason
        if intent.failure_reason == _effect_executor_module.AGENT_NOT_FOUND_REASON:
            # The registry could not produce this name: nothing ran, and
            # nothing will. Skip it for the rest of the rebase rather
            # than paying for it again, and never call it an answer.
            session.terminal_reason = ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
            logger.warning(
                "conflict_resolution: candidate '{}' is not installed in this "
                "workspace; handing over to the next candidate",
                agent_name,
            )
    return intent.failure_reason


def _record_resolution_termination(session: ResolutionSession | None, exc: Exception) -> None:
    """Persist typed invocation failure metadata for the driver's outcome line."""
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
    """Preserve a launch failure rather than relabeling it as a declined candidate."""
    if session is not None:
        session.terminal_reason = ResolutionTerminationReason.EXCEPTION
        session.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE


def _log_resolution_termination(exc: Exception, unresolved_paths: tuple[str, ...]) -> None:
    """Emit an operator-actionable resolution termination diagnostic."""
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


#: Diagnostic keys the WATCHDOG writes from its own typed state. Every
#: other field of an activity event -- ``subagent_activity``,
#: ``current_subagent_tool_call``, ``last_subagent_progress_description``,
#: ``evidence_summary`` -- is the agent's own output quoted back.
_RALPH_AUTHORED_DIAGNOSTIC_KEYS = (
    "last_activity_kind",
    "last_fire_reason",
    "last_deferred_kind",
)


#: How many times one of Ralph's fault markers must appear in an agent's
#: activity before it is treated as Ralph's fault rather than the agent
#: quoting it. A tripped MCP breaker answers EVERY tool call with its
#: 503 frame, so a real transport loop clears this immediately; a
#: resolver that read the token in this repository's source does not.
RALPH_FAULT_ESCALATION_HITS = 5


def _ralph_authored_fault_text(event: object) -> str:
    """Project the part of an activity event Ralph itself wrote.

    Scanning ``str(event)`` scanned the agent's own progress text, and a
    match here marks that agent a dead tool surface for the whole
    rebase. Ralph's fault markers are strings that live in Ralph's
    source, so a resolver reading, grepping or quoting this repository
    -- the commonest thing it does when the conflict IS this repository
    -- got itself killed for repeating a token it had just read, and
    with the shipped one-agent chain that ended conflict resolution for
    the rest of the run. An agent cannot author these keys: the watchdog
    fills them from its own typed state, which is the same projection
    :class:`~ralph.pipeline.conflict_resolution.status.ResolutionStatusReporter`
    already judges liveness from.
    """
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
    """Classify an activity event as Ralph's own fault, on evidence.

    Only Ralph-authored diagnostic fields represent observable infrastructure
    events. Raw agent activity text is unobservable and cannot be trusted for
    exclusion decisions, but any fault pattern found in it is logged for
    diagnostic reporting.
    """
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
    """Escalate Ralph-origin faults from activity events instead of treating them as life."""

    def _observe(event: object) -> None:
        # An event is activity whatever else it carries: the agent is
        # alive and working. Recording that FIRST is what stops a fault
        # tick from discarding a conflict the agent had already resolved.
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
