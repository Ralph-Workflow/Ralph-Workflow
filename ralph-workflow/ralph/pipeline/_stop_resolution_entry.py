from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeGuard

from loguru import logger

from ralph.agents.system_clock import SystemClock
from ralph.git.merge import MERGE_STATE_IN_PROGRESS, merge_state
from ralph.git.rebase.rebase_continuation import rebase_in_progress_at
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline._pending_merge_commit import _git_value
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record
from ralph.pipeline.config_file_fingerprint_watcher import (
    compute_config_fingerprint_for_workspace,
)
from ralph.pipeline.conflict_resolution import ResolutionTerminationReason
from ralph.pipeline.conflict_resolution.attempt_fault import (
    BUDGET_UNCHARGED_TERMINATION_REASONS,
)
from ralph.pipeline.conflict_resolution.resolver_cooldown_text import resolvers_on_cooldown_reason
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
)
from ralph.pipeline.stop_resolution_blocked import StopResolutionBlocked

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.agents.clock import Clock
    from ralph.config.conflict_resolution_config import ConflictResolutionConfig
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.conflict_resolution.session import ResolutionSession
    from ralph.pipeline.integration_actions import AgentRunner
    from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger
    from ralph.recovery.controller import RecoveryController

_WAITING_ACTION_MESSAGE = (
    "WAITING: continuation retry gate active; continuation resolution deferred; conflict "
    "identity, eligible agents, and configuration unchanged. Operator action: run "
    "'ralph --retry' to retry resolution, reset agent backoff, or fix configuration and "
    "run 'ralph --check-config'."
)
_GATE_ACTIVE_REASON = (
    "WAITING: continuation retry gate active; conflict identity and eligible agents unchanged"
)
_KEY_UNAVAILABLE_PREFIX = "WAITING: continuation retry key unavailable"


@dataclass(frozen=True)
class DeferredStopResolution(StopResolutionBlocked):
    failure_cause: ResolutionFailureCause = ResolutionFailureCause.NONE


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
    except (OSError, RuntimeError, ValueError):
        return None if not record.resolving_stop_sha else record.resolving_stop_sha
    return record.resolving_stop_sha or record.pre_target_sha or record.pre_feature_sha


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
    effective_agents = eligible_agents
    if effective_agents is None:
        controller = recovery_controller
        if controller is None:
            from ralph.pipeline.effect_executor import current_agent_effect_dependencies

            controller = current_agent_effect_dependencies().recovery_controller
        if controller is None and config is not None:
            return KeyUnavailable(
                "recovery controller required; pass the session/pipeline_deps controller "
                "or an explicit recovery_controller"
            )
        if controller is not None:
            try:
                candidates = (
                    chain_agents if chain_agents is not None else controller.agents_for_phase(phase)
                )
                eligibility = controller.chain_eligibility(phase, candidates)
                effective_agents = tuple(
                    sorted(agent for agent, status in eligibility if status.is_eligible)
                )
            except (OSError, RuntimeError, ValueError, KeyError) as exc:
                return KeyUnavailable(f"agent eligibility unavailable: {exc}")
        else:
            effective_agents = ()
    try:
        fingerprint = (
            config_fingerprint
            if config_fingerprint is not None
            else compute_config_fingerprint_for_workspace(root, config_path)
        )
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
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
    if trigger.name != "rebase_stop":
        raise TypeError(
            f"trigger {trigger.name!r} requires a launcher; no inline merge-stop execution exists"
        )
    resolver = trigger.resolver
    if _is_rebase_stop_resolver(resolver):
        return _resolve_rebase_with_config(
            trigger.root,
            trigger.target,
            resolver,
            trigger.conflict_config,
            trigger.agent_runner,
            trigger.record,
            trigger=trigger,
        )
    raise TypeError(f"expected callable RebaseStopResolver, got {resolver!r}")


def _gate_record(trigger: StopResolutionTrigger) -> IntegrationRecord:
    if trigger.record is not None:
        return trigger.record
    return resolution_gate_record(trigger.root, trigger.target)


def resolution_gate_record(root: Path, target: str) -> IntegrationRecord:
    record = read_record(root)
    if record is not None:
        return record
    from ralph.git.rebase.rebase_continuation import (
        RebaseContinuationError,
        rebase_in_progress_at,
    )
    from ralph.pipeline.conflict_resolution.rebase_loop import current_rebase_identity

    try:
        in_rebase = rebase_in_progress_at(root)
    except (RebaseContinuationError, OSError, RuntimeError, ValueError):
        in_rebase = False

    if in_rebase:
        with suppress(OSError, RuntimeError, ValueError):
            orig_head, onto = current_rebase_identity(root)
            return IntegrationRecord(
                phase="integrating",
                target=target,
                pre_feature_sha=orig_head,
                pre_target_sha=onto,
                resolving_rebase=True,
            )
    in_merge = False
    with suppress(OSError, RuntimeError, ValueError):
        in_merge = merge_state(root) == MERGE_STATE_IN_PROGRESS
    return IntegrationRecord(
        phase="integrating",
        target=target,
        pre_feature_sha=_git_value(root, "rev-parse", "--verify", "HEAD") or "",
        pre_target_sha=_git_value(root, "rev-parse", "--verify", "MERGE_HEAD"),
        resolving_merge=in_merge,
    )


def _attempt_cause_from_outcome(
    outcome: StopResolutionOutcome,
    trigger: StopResolutionTrigger,
) -> ResolutionFailureCause:
    if (
        isinstance(outcome, (ResolvedOutcome, RetryLaterOutcome))
        and outcome.failure_cause is not None
    ):
        return outcome.failure_cause
    if isinstance(outcome, ConfigFailureOutcome):
        return ResolutionFailureCause.CONFIG_FAILURE
    return _deferred_failure_cause(trigger.root, trigger.record)


def _failure_reason_from_outcome(outcome: StopResolutionOutcome) -> str | None:
    if isinstance(outcome, RetryLaterOutcome):
        return outcome.on_change
    if isinstance(outcome, ConfigFailureOutcome):
        return f"fix {outcome.file} then run ralph --check-config"
    if isinstance(outcome, WaitingOutcome):
        return outcome.external_condition
    if isinstance(outcome, NextStrategyOutcome):
        return outcome.strategy
    if isinstance(outcome, UnknownOutcome):
        return str(outcome.error)
    return None


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
    if decision.blocked and (not trigger.force_retry or not key.eligible_agents):
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

    _adopt_owned_rebase(trigger)
    result = _execute_trigger(trigger)
    post_key = _post_attempt_key(stop_identity, trigger, key)
    _record_post_attempt(trigger, post_key, result)
    return result


def _adopt_owned_rebase(trigger: StopResolutionTrigger) -> None:
    if trigger.record is not None:
        return
    _adopt_active_rebase_record(trigger.root, trigger.target)


def _adopt_active_rebase_record(
    workspace_root: Path,
    target: str,
) -> IntegrationRecord | None:
    if not target.strip():
        return None
    try:
        if read_record(workspace_root) is not None:
            return None
        from ralph.pipeline.conflict_resolution.rebase_loop import current_rebase_identity

        if not rebase_in_progress_at(workspace_root):
            return None
        original, onto = current_rebase_identity(workspace_root)
        if not original or not onto:
            return None
        record = IntegrationRecord(
            phase="integrating",
            target=target,
            pre_feature_sha=original,
            pre_target_sha=onto,
            resolving_rebase=True,
        )
        write_record(workspace_root, record)
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        logger.warning(
            "auto_integrate: could not adopt the active rebase on '{}' into the durable record: {}",
            target,
            exc,
        )
        return None
    return record


adopt_resolving_rebase_record = _adopt_active_rebase_record


def _is_stop_resolution_outcome(value: object) -> TypeGuard[StopResolutionOutcome]:
    return isinstance(
        value,
        (
            ResolvedOutcome,
            WaitingOutcome,
            RetryLaterOutcome,
            NextStrategyOutcome,
            ConfigFailureOutcome,
            UnknownOutcome,
        ),
    )


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
            if (
                isinstance(result, RebaseResolutionResult)
                and result.terminal_reason is None
                and result.failure_cause == ResolutionFailureCause.NONE
            ):
                return
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


def _warn_conflict_budget_uncharged(target: str, session: ResolutionSession) -> None:
    if session.terminal_reason is None:
        return
    if session.charge_conflict_budget and (
        session.terminal_reason not in BUDGET_UNCHARGED_TERMINATION_REASONS
    ):
        return
    logger.warning(
        "auto_integrate: rebase conflict resolution for '{}' ended on {}, which does not "
        "charge the conflict budget; not recording the conflict as unresolvable",
        target,
        session.terminal_reason.value if session.terminal_reason else "no terminal evidence",
    )


def _determine_uncharged_reason(
    session: ResolutionSession, carried_cause: ResolutionFailureCause
) -> str | None:
    if session.terminal_reason == ResolutionTerminationReason.OUT_OF_REACH:
        reason = session.operator_action_message or session.halt_reason
        if not reason and session.unresolved_paths:
            from ralph.pipeline.conflict_resolution.sight import gitlink_escalation_message

            return gitlink_escalation_message(session.unresolved_paths)
        return reason
    if session.terminal_reason == ResolutionTerminationReason.NO_RUNNABLE_CANDIDATE:
        reason = session.operator_action_message or session.halt_reason
        if not reason or "resume point:" not in reason:
            from ralph.pipeline.conflict_resolution._operator_command import (
                operator_command_for_paths,
            )

            paths = tuple(session.unresolved_paths)
            return resolvers_on_cooldown_reason(paths, operator_command_for_paths(paths))
        return reason
    if carried_cause in (
        ResolutionFailureCause.CONFIG_FAILURE,
        ResolutionFailureCause.CONTEXT_FAILURE,
    ):
        return session.halt_reason or (
            session.terminal_reason.value if session.terminal_reason is not None else None
        )
    return None


def _uncharged_resolution_outcome(
    session: ResolutionSession,
    carried_cause: ResolutionFailureCause,
) -> tuple[StopResolutionOutcome, str | None]:
    reason = _determine_uncharged_reason(session, carried_cause)
    outcome: StopResolutionOutcome
    if session.last_resolution_outcome is not None and not isinstance(
        session.last_resolution_outcome, ResolvedOutcome
    ):
        outcome = session.last_resolution_outcome
    else:
        outcome = classify_resolution_failure(
            terminal_reason=session.terminal_reason,
            failure_cause=carried_cause,
            reason=reason,
            paths=session.unresolved_paths,
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


def _handle_uncharged_session(
    root: Path,
    target: str,
    session: ResolutionSession,
    carried_cause: ResolutionFailureCause,
    record: IntegrationRecord | None,
) -> RebaseResolutionResult:
    _warn_conflict_budget_uncharged(target, session)
    outcome, reason = _uncharged_resolution_outcome(session, carried_cause)
    _persist_resolution_cause(root, carried_cause, reason=reason, record=record)
    return RebaseResolutionResult(outcome, reason, carried_cause, session.terminal_reason)


def _handle_charged_session(
    root: Path,
    session: ResolutionSession,
    carried_cause: ResolutionFailureCause,
    record: IntegrationRecord | None,
) -> RebaseResolutionResult:
    reason = (
        session.operator_action_message
        if session.operator_action_message and "resume point:" in session.operator_action_message
        else session.halt_reason
    )
    if session.terminal_reason == ResolutionTerminationReason.NO_RUNNABLE_CANDIDATE and (
        not reason or "resume point:" not in reason
    ):
        from ralph.pipeline.conflict_resolution._operator_command import operator_command_for_paths

        paths = tuple(session.unresolved_paths)
        reason = resolvers_on_cooldown_reason(paths, operator_command_for_paths(paths))
    effective_cause = (
        carried_cause
        if carried_cause != ResolutionFailureCause.NONE
        else ResolutionFailureCause.UNRESOLVED_CONFLICT
    )
    outcome: StopResolutionOutcome
    if session.terminal_reason == ResolutionTerminationReason.NO_RUNNABLE_CANDIDATE:
        outcome = RetryLaterOutcome(
            on_change=reason or "resolver_agent_eligibility_changed",
            terminal_reason=ResolutionTerminationReason.NO_RUNNABLE_CANDIDATE,
            unresolved_paths=tuple(session.unresolved_paths),
            failure_cause=effective_cause,
        )
    elif session.last_resolution_outcome is not None and not isinstance(
        session.last_resolution_outcome, ResolvedOutcome
    ):
        outcome = session.last_resolution_outcome
    else:
        outcome = classify_resolution_failure(
            terminal_reason=session.terminal_reason,
            failure_cause=effective_cause,
            reason=reason,
            paths=session.unresolved_paths,
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
    *,
    trigger: StopResolutionTrigger | None = None,
) -> RebaseResolutionResult:
    session = _create_resolution_session(config)
    if _execute_rebase_resolution(root, target, resolver, session, agent_runner):
        return RebaseResolutionResult(
            ResolvedOutcome(), None, ResolutionFailureCause.NONE, session.terminal_reason
        )

    carried_cause = _session_carried_cause(session)
    if (
        not session.charge_conflict_budget
        or session.terminal_reason in BUDGET_UNCHARGED_TERMINATION_REASONS
    ):
        return _handle_uncharged_session(root, target, session, carried_cause, record)
    return _handle_charged_session(root, session, carried_cause, record)


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
