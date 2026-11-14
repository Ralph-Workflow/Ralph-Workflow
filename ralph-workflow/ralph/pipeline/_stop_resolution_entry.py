from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypeGuard

from loguru import logger

from ralph.agents.system_clock import SystemClock
from ralph.git.merge import MERGE_STATE_IN_PROGRESS, merge_state
from ralph.git.rebase.rebase_continuation import rebase_in_progress_at
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline._pending_merge_commit import _git_value
from ralph.pipeline.agent_entry_point import AgentEntryPoint
from ralph.pipeline.attempt_runner import build_entry_point_recovery_controller
from ralph.pipeline.attempt_runner_deps import PRODUCTION_INVOKE, AttemptRunnerDeps
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.config_file_fingerprint_watcher import (
    compute_config_fingerprint_for_workspace,
)
from ralph.pipeline.conflict_resolution import ResolutionTerminationReason
from ralph.pipeline.conflict_resolution.attempt_fault import (
    RESOLVER_NOT_SPENT_TERMINATION_REASONS,
)
from ralph.pipeline.key_unavailable import KeyUnavailable
from ralph.pipeline.rebase_resolution_result import RebaseResolutionResult
from ralph.pipeline.resolution_attempt_key import ResolutionAttemptKey
from ralph.pipeline.resolution_gate_decision import ResolutionGateDecision
from ralph.pipeline.resolution_gate_facts import ResolutionGateFacts
from ralph.pipeline.resolution_outcome import (
    ConfigFailureOutcome,
    NextStrategyOutcome,
    ResolvedOutcome,
    RetryLaterOutcome,
    StopResolutionOutcome,
    UnknownOutcome,
    WaitingOutcome,
)
from ralph.pipeline.resolution_outcome_producer import (
    classify_resolution_failure,
    stop_resolution_outcome_from_legacy,
)
from ralph.pipeline.stop_resolution_blocked import StopResolutionBlocked

if TYPE_CHECKING:
    from ralph.agents.clock import Clock
    from ralph.config.conflict_resolution_config import ConflictResolutionConfig
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.conflict_resolution.session import ResolutionSession
    from ralph.pipeline.integration_actions import AgentRunner
    from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger
    from ralph.recovery.controller import RecoveryController

_WAITING_ACTION_MESSAGE = (
    "WAITING: continuation resolution deferred; conflict identity, eligible agents, and "
    "configuration unchanged. Operator action: run 'ralph --retry' to retry resolution, "
    "reset agent backoff, or fix configuration and run 'ralph --check-config'."
)
_GATE_ACTIVE_REASON = (
    "WAITING: continuation retry gate active; conflict identity and eligible agents unchanged"
)
_KEY_UNAVAILABLE_PREFIX = "WAITING: continuation retry key unavailable"


@dataclass(frozen=True)
class DeferredStopResolution(StopResolutionBlocked):
    failure_cause: ResolutionFailureCause = ResolutionFailureCause.NONE


_PRESENTED_ROUNDS_CAP: int = 256


class _BoundedPresentedRounds(OrderedDict[Path, str]):
    def __init__(
        self,
        *args: object,
        cap: int = _PRESENTED_ROUNDS_CAP,
        **kwargs: object,
    ) -> None:
        if cap <= 0:
            raise ValueError(f"cap must be positive, got {cap}")
        self._cap = cap
        super().__init__(*args, **kwargs)
        while len(self) > self._cap:
            self.popitem(last=False)

    def __setitem__(self, key: Path, value: str) -> None:
        super().__setitem__(key, value)
        self.move_to_end(key)
        while len(self) > self._cap:
            self.popitem(last=False)


_presented_rounds: _BoundedPresentedRounds = _BoundedPresentedRounds()


def retained_round_is_new(root: Path, fingerprint: str) -> bool:
    resolved = root.resolve()
    if _presented_rounds.get(resolved) == fingerprint:
        return False
    _presented_rounds[resolved] = fingerprint
    return True


def reset_stop_resolution_presentation() -> None:
    _presented_rounds.clear()


def decide_resolution_attempt(facts: ResolutionGateFacts) -> ResolutionGateDecision:
    key = ResolutionAttemptKey(
        facts.stop_identity,
        tuple(sorted(facts.eligible_agents)),
        facts.config_fingerprint,
    )
    blocked = facts.stored_key is not None and tuple(facts.stored_key) == tuple(key)
    return ResolutionGateDecision(
        key=key,
        blocked=blocked,
        reason=_GATE_ACTIVE_REASON if blocked else None,
        observed_at_ms=facts.observed_at_ms,
    )


def _current_stop_identity(root: Path, record: IntegrationRecord) -> str | None:
    try:
        if record.resolving_merge and merge_state(root) == MERGE_STATE_IN_PROGRESS:
            return _git_value(root, "rev-parse", "--verify", "MERGE_HEAD") or record.pre_target_sha
        if rebase_in_progress_at(root):
            return (
                _git_value(root, "rev-parse", "--verify", "REBASE_HEAD")
                or record.resolving_stop_sha
                or record.pre_target_sha
            )
    except Exception:
        return None if not record.resolving_stop_sha else record.resolving_stop_sha
    return record.resolving_stop_sha or record.pre_target_sha or record.pre_feature_sha


def _controller_for_key(
    root: Path,
    config: UnifiedConfig | None,
    recovery_controller: RecoveryController | None,
) -> RecoveryController | KeyUnavailable | None:
    if recovery_controller is not None or config is None:
        return recovery_controller
    try:
        from ralph.pipeline.runner import load_policy_bundle_for_run
        from ralph.workspace.scope import WorkspaceScope

        policy_bundle = load_policy_bundle_for_run(WorkspaceScope(root=root), config)
        return build_entry_point_recovery_controller(
            AgentEntryPoint.INTEGRATION_CONTINUATION,
            config=config,
            policy_bundle=policy_bundle,
            deps=AttemptRunnerDeps(
                clock=SystemClock(),
                invoke=PRODUCTION_INVOKE,
                probe_runner=None,
                workspace_probe=None,
                connectivity=None,
            ),
        )
    except Exception as exc:
        return KeyUnavailable(f"recovery controller unavailable: {exc}")


def compute_resolution_attempt_key(
    root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None = None,
    *,
    recovery_controller: RecoveryController | None = None,
    phase: str = "rebase_conflict_resolution",
    chain_agents: tuple[str, ...] | None = None,
    config_path: Path | None = None,
    stop_identity: str | None = None,
    eligible_agents: tuple[str, ...] | None = None,
    config_fingerprint: str | None = None,
) -> ResolutionAttemptKey | KeyUnavailable:
    stop_id = stop_identity if stop_identity is not None else _current_stop_identity(root, record)
    controller = _controller_for_key(root, config, recovery_controller)
    if isinstance(controller, KeyUnavailable):
        return controller
    effective_agents = eligible_agents
    if effective_agents is None and controller is not None:
        try:
            candidates = (
                chain_agents if chain_agents is not None else controller.agents_for_phase(phase)
            )
            eligibility = controller.chain_eligibility(phase, candidates)
            effective_agents = tuple(
                sorted(agent for agent, status in eligibility if status.is_eligible)
            )
        except Exception as exc:
            return KeyUnavailable(f"agent eligibility unavailable: {exc}")
    if effective_agents is None:
        effective_agents = ()
    try:
        fingerprint = (
            config_fingerprint
            if config_fingerprint is not None
            else compute_config_fingerprint_for_workspace(root, config_path)
        )
    except Exception as exc:
        return KeyUnavailable(f"configuration fingerprint unavailable: {exc}")
    return ResolutionAttemptKey(stop_id, tuple(sorted(effective_agents)), fingerprint)


def _stored_key(
    root: Path, record: IntegrationRecord
) -> tuple[str | None, tuple[str, ...], str | None] | None:
    del root
    return record.resolution_gate_key


def _clock_ms(clock: Clock | None) -> int:
    effective = clock if clock is not None else SystemClock()
    return int(effective.monotonic() * 1000)


def _deferred_failure_cause(root: Path, record: IntegrationRecord | None) -> ResolutionFailureCause:
    current = read_record(root) or record
    if current is None:
        return ResolutionFailureCause.NONE
    return current.last_failure_cause


def _present_block(
    root: Path,
    record: IntegrationRecord | None,
    key: ResolutionAttemptKey,
) -> None:
    current = read_record(root) or record
    serialized_key = (key.stop_identity, key.eligible_agents, key.config_fingerprint)
    if current is not None and current.waiting_notification_key == serialized_key:
        return
    logger.bind(recovery=True).info("{}", _WAITING_ACTION_MESSAGE)
    if current is not None:
        write_record(root, current.with_waiting_notification_key(serialized_key))


def _record_failed_attempt(
    trigger: StopResolutionTrigger,
    key: ResolutionAttemptKey,
    failure_cause: ResolutionFailureCause,
    failure_reason: str | None = None,
) -> None:
    if trigger.record is None:
        return
    current = read_record(trigger.root) or trigger.record
    effective_cause = (
        failure_cause
        if failure_cause != ResolutionFailureCause.NONE
        else ResolutionFailureCause.UNRESOLVED_CONFLICT
    )
    updates: dict[str, object] = {
        "resolution_gate_key": tuple(key),
        "last_failure_cause": effective_cause,
    }
    if failure_reason is not None and not (
        current.rebase_continue_pending or current.rebase_continue_error
    ):
        updates["rebase_continue_error"] = failure_reason
    write_record(
        trigger.root,
        current.model_copy(update=updates),
    )


def _post_attempt_key(
    stop_identity: str | None,
    trigger: StopResolutionTrigger,
    fallback: ResolutionAttemptKey,
) -> ResolutionAttemptKey:
    if trigger.record is None or trigger.key_provider is not None:
        return fallback
    computed = compute_resolution_attempt_key(
        trigger.root,
        read_record(trigger.root) or trigger.record,
        trigger.config,
        recovery_controller=trigger.recovery_controller,
        phase=trigger.phase,
        chain_agents=trigger.chain_agents,
        config_path=trigger.config_path,
        stop_identity=stop_identity,
        eligible_agents=trigger.eligible_agents,
        config_fingerprint=trigger.config_fingerprint,
    )
    return computed if isinstance(computed, ResolutionAttemptKey) else fallback


def _is_rebase_stop_resolver(resolver: object) -> TypeGuard[RebaseStopResolver]:
    return callable(resolver)


def _is_conflict_resolver(resolver: object) -> TypeGuard[ConflictResolver]:
    return callable(resolver)


class _ProductionRebaseRunner:
    def resolve_rebase(
        self,
        root: Path,
        target: str,
        resolver: RebaseStopResolver,
        *,
        session: ResolutionSession | None = None,
    ) -> bool:
        from ralph.pipeline import auto_integrate_rebase_merge as merge_mod

        return bool(merge_mod.resolve_rebase_in_progress(root, target, resolver, session=session))


_PRODUCTION_REBASE_RUNNER = _ProductionRebaseRunner()


def production_rebase_runner() -> AgentRunner:
    return _PRODUCTION_REBASE_RUNNER


def _execute_trigger(trigger: StopResolutionTrigger) -> object:
    if trigger.launcher is not None:
        return trigger.launcher()
    if trigger.name == "rebase_stop":
        resolver = trigger.resolver
        if _is_rebase_stop_resolver(resolver):
            return _resolve_rebase_with_config(
                trigger.root,
                trigger.target,
                resolver,
                trigger.conflict_config,
                trigger.agent_runner,
                trigger.record,
            )
        raise TypeError(f"expected callable RebaseStopResolver, got {resolver!r}")

    from ralph.pipeline.auto_integrate_resolve import resolve_and_commit_with_reason

    resolver = trigger.resolver
    if _is_conflict_resolver(resolver):
        return resolve_and_commit_with_reason(
            trigger.root,
            trigger.target,
            resolver,
        )
    raise TypeError(f"expected callable ConflictResolver, got {resolver!r}")


def _gate_record(trigger: StopResolutionTrigger) -> IntegrationRecord:
    if trigger.record is not None:
        return trigger.record
    from ralph.pipeline.auto_integrate_resolve import resolution_gate_record

    return resolution_gate_record(trigger.root, trigger.target)


def _record_outcome_attempt(
    trigger: StopResolutionTrigger,
    key: ResolutionAttemptKey,
    outcome: StopResolutionOutcome,
) -> None:
    attempt_cause: ResolutionFailureCause = ResolutionFailureCause.NONE
    if isinstance(outcome, (ResolvedOutcome, RetryLaterOutcome)):
        if outcome.failure_cause is not None:
            attempt_cause = outcome.failure_cause
    elif isinstance(outcome, ConfigFailureOutcome):
        attempt_cause = ResolutionFailureCause.CONFIG_FAILURE
    if attempt_cause == ResolutionFailureCause.NONE:
        attempt_cause = _deferred_failure_cause(trigger.root, trigger.record)
    failure_reason: str | None = None
    if isinstance(outcome, RetryLaterOutcome):
        failure_reason = outcome.on_change
    elif isinstance(outcome, ConfigFailureOutcome):
        failure_reason = f"fix {outcome.file} then run ralph --check-config"
    elif isinstance(outcome, WaitingOutcome):
        failure_reason = outcome.external_condition
    elif isinstance(outcome, NextStrategyOutcome):
        failure_reason = outcome.strategy
    elif isinstance(outcome, UnknownOutcome):
        failure_reason = str(outcome.error)
    _record_failed_attempt(trigger, key, attempt_cause, failure_reason=failure_reason)


def attempt_stop_resolution(
    stop_identity: str | None,
    trigger: StopResolutionTrigger,
) -> object:
    from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger

    if not isinstance(trigger, StopResolutionTrigger):
        raise TypeError("trigger must be a StopResolutionTrigger")
    record = _gate_record(trigger)
    computed = (
        trigger.key_provider()
        if trigger.key_provider is not None
        else compute_resolution_attempt_key(
            trigger.root,
            record,
            trigger.config,
            recovery_controller=trigger.recovery_controller,
            phase=trigger.phase,
            chain_agents=trigger.chain_agents,
            config_path=trigger.config_path,
            stop_identity=stop_identity,
            eligible_agents=trigger.eligible_agents,
            config_fingerprint=trigger.config_fingerprint,
        )
    )
    if isinstance(computed, KeyUnavailable):
        return StopResolutionBlocked(f"{_KEY_UNAVAILABLE_PREFIX}: {computed.reason}", computed)
    key = computed
    decision = decide_resolution_attempt(
        ResolutionGateFacts(
            stored_key=_stored_key(trigger.root, record),
            stop_identity=key.stop_identity,
            eligible_agents=key.eligible_agents,
            config_fingerprint=key.config_fingerprint,
            observed_at_ms=_clock_ms(trigger.clock),
        )
    )
    if decision.blocked and not trigger.force_retry:
        carried_cause = _deferred_failure_cause(trigger.root, trigger.record)
        _present_block(trigger.root, trigger.record, decision.key)
        current = read_record(trigger.root) or trigger.record
        gate_reason = decision.reason or _GATE_ACTIVE_REASON
        blocked_reason = (
            current.rebase_continue_error
            if current is not None
            and current.rebase_continue_error
            and current.rebase_continue_error.startswith("WAITING:")
            else gate_reason
        )
        return DeferredStopResolution(
            blocked_reason,
            decision.key,
            carried_cause,
        )

    result = _execute_trigger(trigger)
    post_key = _post_attempt_key(stop_identity, trigger, key)
    _record_post_attempt(trigger, post_key, result)
    return result


def _record_post_attempt(
    trigger: StopResolutionTrigger,
    post_key: ResolutionAttemptKey,
    result: object,
) -> None:
    eff_outcome = result.outcome if isinstance(result, RebaseResolutionResult) else result
    if isinstance(
        eff_outcome,
        (
            ResolvedOutcome,
            WaitingOutcome,
            RetryLaterOutcome,
            NextStrategyOutcome,
            ConfigFailureOutcome,
            UnknownOutcome,
        ),
    ):
        if not isinstance(eff_outcome, ResolvedOutcome):
            _record_outcome_attempt(trigger, post_key, eff_outcome)
    else:
        from ralph.git.merge import MergeResult

        if isinstance(result, MergeResult) and result.outcome != "success":
            _record_failed_attempt(
                trigger,
                post_key,
                result.failure_cause,
                failure_reason=result.reason,
            )


def _warn_chain_not_spent(target: str, session: ResolutionSession) -> None:
    if session.charge_conflict_budget and (
        session.terminal_reason not in RESOLVER_NOT_SPENT_TERMINATION_REASONS
    ):
        return
    logger.warning(
        "auto_integrate: rebase conflict resolution for '{}' ended on {} without the "
        "resolver chain being spent; not recording it as exhausted",
        target,
        session.terminal_reason.value if session.terminal_reason else "no terminal evidence",
    )


def _determine_not_spent_reason(
    session: ResolutionSession, carried_cause: ResolutionFailureCause
) -> str | None:
    if session.terminal_reason == ResolutionTerminationReason.OUT_OF_REACH:
        reason = session.operator_action_message or session.exhaustion_reason
        if not reason and session.unresolved_paths:
            from ralph.pipeline.conflict_resolution.sight import gitlink_escalation_message

            return gitlink_escalation_message(session.unresolved_paths)
        return reason
    if session.terminal_reason == ResolutionTerminationReason.CANDIDATE_EXHAUSTED:
        reason = session.operator_action_message or session.exhaustion_reason
        if not reason or "needs operator action:" not in reason:
            from ralph.pipeline.conflict_resolution._operator_command import (
                operator_command_for_paths,
            )

            paths = tuple(session.unresolved_paths)
            return (
                "all resolver agents are spent/unavailable; conflict on "
                f"{', '.join(paths)} needs operator action: {operator_command_for_paths(paths)}"
            )
        return reason
    if carried_cause in (
        ResolutionFailureCause.CONFIG_FAILURE,
        ResolutionFailureCause.CONTEXT_FAILURE,
    ):
        return session.exhaustion_reason or (
            session.terminal_reason.value if session.terminal_reason is not None else None
        )
    return None


def _not_spent_resolution_outcome(
    session: ResolutionSession,
    carried_cause: ResolutionFailureCause,
) -> tuple[StopResolutionOutcome, str | None]:
    reason = _determine_not_spent_reason(session, carried_cause)
    outcome: StopResolutionOutcome
    if session.last_resolution_outcome is not None and not isinstance(
        session.last_resolution_outcome, ResolvedOutcome
    ):
        outcome = session.last_resolution_outcome
    else:
        outcome = stop_resolution_outcome_from_legacy(
            classify_resolution_failure(
                terminal_reason=session.terminal_reason,
                failure_cause=carried_cause,
                reason=reason,
                paths=session.unresolved_paths,
            )
        )
    return outcome, reason


def _create_resolution_session(
    config: ConflictResolutionConfig | None,
) -> ResolutionSession:
    from ralph.pipeline.conflict_resolution.session import ResolutionSession

    if config is None:
        return ResolutionSession()
    return ResolutionSession(
        inactivity_timeout_seconds=config.inactivity_timeout_seconds,
        max_rounds_per_stop=config.max_rounds_per_stop,
        max_rebase_conflict_stops=config.max_rebase_conflict_stops,
        max_fallback_agents=config.max_fallback_agents,
    )


def _execute_rebase_resolution(
    root: Path,
    target: str,
    resolver: RebaseStopResolver,
    session: ResolutionSession,
    agent_runner: AgentRunner | None,
) -> bool:
    from ralph.pipeline.conflict_resolution.rebase_loop import (
        bind_active_rebase_resolution_session,
    )

    with bind_active_rebase_resolution_session(session):
        if agent_runner is not None:
            return agent_runner.resolve_rebase(root, target, resolver, session=session)
        return production_rebase_runner().resolve_rebase(root, target, resolver, session=session)


def _session_carried_cause(session: ResolutionSession) -> ResolutionFailureCause:
    raw_cause: object = session.last_failure_cause
    carried_cause = (
        raw_cause if isinstance(raw_cause, ResolutionFailureCause) else ResolutionFailureCause.NONE
    )
    if carried_cause == ResolutionFailureCause.NONE:
        if session.terminal_reason == ResolutionTerminationReason.CONFIG_FAILURE:
            return ResolutionFailureCause.CONFIG_FAILURE
        if session.terminal_reason == ResolutionTerminationReason.CONTEXT_FAILURE:
            return ResolutionFailureCause.CONTEXT_FAILURE
    return carried_cause


def _handle_unspent_session(
    root: Path,
    target: str,
    session: ResolutionSession,
    carried_cause: ResolutionFailureCause,
    record: IntegrationRecord | None,
) -> RebaseResolutionResult:
    _warn_chain_not_spent(target, session)
    outcome, reason = _not_spent_resolution_outcome(session, carried_cause)
    _persist_resolution_cause(root, carried_cause, reason=reason, record=record)
    return RebaseResolutionResult(outcome, reason, carried_cause, session.terminal_reason)


def _handle_spent_session(
    root: Path,
    session: ResolutionSession,
    carried_cause: ResolutionFailureCause,
    record: IntegrationRecord | None,
) -> RebaseResolutionResult:
    reason = (
        session.operator_action_message
        if session.operator_action_message
        and "needs operator action:" in session.operator_action_message
        else session.exhaustion_reason
    )
    if session.terminal_reason == ResolutionTerminationReason.CANDIDATE_EXHAUSTED and (
        not reason or "needs operator action:" not in reason
    ):
        from ralph.pipeline.conflict_resolution._operator_command import operator_command_for_paths

        paths = tuple(session.unresolved_paths)
        reason = (
            "all resolver agents are spent/unavailable; conflict on "
            f"{', '.join(paths)} needs operator action: {operator_command_for_paths(paths)}"
        )
    effective_cause = (
        carried_cause
        if carried_cause != ResolutionFailureCause.NONE
        else ResolutionFailureCause.UNRESOLVED_CONFLICT
    )
    outcome: StopResolutionOutcome
    if session.terminal_reason == ResolutionTerminationReason.CANDIDATE_EXHAUSTED:
        outcome = RetryLaterOutcome(
            on_change=reason or "resolver_agent_eligibility_changed",
            terminal_reason=ResolutionTerminationReason.CANDIDATE_EXHAUSTED,
            unresolved_paths=tuple(session.unresolved_paths),
            failure_cause=effective_cause,
        )
    elif session.last_resolution_outcome is not None and not isinstance(
        session.last_resolution_outcome, ResolvedOutcome
    ):
        outcome = session.last_resolution_outcome
    else:
        outcome = stop_resolution_outcome_from_legacy(
            classify_resolution_failure(
                terminal_reason=session.terminal_reason,
                failure_cause=effective_cause,
                reason=reason,
                paths=session.unresolved_paths,
            )
        )
    _persist_resolution_cause(root, effective_cause, reason=reason, record=record)
    return RebaseResolutionResult(
        outcome,
        reason,
        effective_cause,
        session.terminal_reason,
    )


def _resolve_rebase_with_config(
    root: Path,
    target: str,
    resolver: RebaseStopResolver,
    config: ConflictResolutionConfig | None,
    agent_runner: AgentRunner | None = None,
    record: IntegrationRecord | None = None,
) -> RebaseResolutionResult:
    session = _create_resolution_session(config)
    if _execute_rebase_resolution(root, target, resolver, session, agent_runner):
        return RebaseResolutionResult(
            ResolvedOutcome(), None, ResolutionFailureCause.NONE, session.terminal_reason
        )

    carried_cause = _session_carried_cause(session)
    if (
        not session.charge_conflict_budget
        or session.terminal_reason in RESOLVER_NOT_SPENT_TERMINATION_REASONS
    ):
        return _handle_unspent_session(root, target, session, carried_cause, record)
    return _handle_spent_session(root, session, carried_cause, record)


def _persist_resolution_cause(
    root: Path,
    cause: ResolutionFailureCause,
    reason: str | None = None,
    record: IntegrationRecord | None = None,
) -> None:
    current = read_record(root) or record
    if current is not None:
        updates: dict[str, object] = {"last_failure_cause": cause}
        if reason is not None and not (
            current.rebase_continue_pending or current.rebase_continue_error
        ):
            updates["rebase_continue_error"] = reason
        write_record(root, current.model_copy(update=updates))
