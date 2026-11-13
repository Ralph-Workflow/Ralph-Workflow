from __future__ import annotations

from typing import TYPE_CHECKING, cast

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
from ralph.pipeline.stop_resolution_blocked import StopResolutionBlocked

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.agents.clock import Clock
    from ralph.config.conflict_resolution_config import ConflictResolutionConfig
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
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


_presented_rounds: dict[Path, str] = {}


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
            candidates = list(chain_agents) if chain_agents is not None else []
            if chain_agents is None:
                candidates_set: set[str] = set()
                chain = controller._get_chain_agents(phase)
                if chain is not None:
                    candidates_set.update(chain)
                snapshot = controller.snapshot()
                for field in ("unavailable_timeouts", "backoff_attempts"):
                    values = snapshot.get(field)
                    if isinstance(values, dict):
                        candidates_set.update(str(value) for value in values)
                candidates = sorted(candidates_set)
            effective_agents = tuple(sorted(controller.agents_now_available(phase, candidates)))
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
    if record.resolution_gate_key is not None:
        return record.resolution_gate_key
    current = read_record(root)
    return None if current is None else current.resolution_gate_key


def _clock_ms(clock: Clock | None) -> int:
    effective = clock if clock is not None else SystemClock()
    return int(effective.monotonic() * 1000)


def _present_block(root: Path, record: IntegrationRecord, key: ResolutionAttemptKey) -> None:
    current = read_record(root) or record
    serialized_key = (key.stop_identity, key.eligible_agents, key.config_fingerprint)
    if current.waiting_notification_key == serialized_key:
        return
    logger.bind(recovery=True).info("{}", _WAITING_ACTION_MESSAGE)
    write_record(root, current.with_waiting_notification_key(serialized_key))


def _record_failed_attempt(
    trigger: StopResolutionTrigger,
    key: ResolutionAttemptKey,
    failure_cause: ResolutionFailureCause,
) -> None:
    if trigger.record is None:
        return
    current = read_record(trigger.root) or trigger.record
    effective_cause = (
        failure_cause
        if failure_cause != ResolutionFailureCause.NONE
        else ResolutionFailureCause.UNRESOLVED_CONFLICT
    )
    write_record(
        trigger.root,
        current.model_copy(
            update={"resolution_gate_key": tuple(key), "last_failure_cause": effective_cause}
        ),
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


def attempt_stop_resolution(
    stop_identity: str | None,
    trigger: StopResolutionTrigger,
) -> object:
    from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger

    if not isinstance(trigger, StopResolutionTrigger):
        raise TypeError("trigger must be a StopResolutionTrigger")
    key: ResolutionAttemptKey | None = None
    if trigger.record is not None:
        computed = (
            trigger.key_provider()
            if trigger.key_provider is not None
            else compute_resolution_attempt_key(
                trigger.root,
                trigger.record,
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
                stored_key=_stored_key(trigger.root, trigger.record),
                stop_identity=key.stop_identity,
                eligible_agents=key.eligible_agents,
                config_fingerprint=key.config_fingerprint,
                observed_at_ms=_clock_ms(trigger.clock),
            )
        )
        if decision.blocked and not trigger.force_retry:
            _present_block(trigger.root, trigger.record, decision.key)
            return StopResolutionBlocked(decision.reason or _GATE_ACTIVE_REASON, decision.key)

    if trigger.launcher is not None:
        result = trigger.launcher()
    elif trigger.name == "rebase_stop":
        result = _resolve_rebase_with_config(
            trigger.root,
            trigger.target,
            cast("RebaseStopResolver", trigger.resolver),
            trigger.conflict_config,
            trigger.agent_runner,
        )
    else:
        from ralph.pipeline.auto_integrate_resolve import resolve_and_commit_with_reason

        result = resolve_and_commit_with_reason(
            trigger.root,
            trigger.target,
            cast("ConflictResolver", trigger.resolver),
        )
    if key is not None:
        post_key = _post_attempt_key(stop_identity, trigger, key)
        if isinstance(result, RebaseResolutionResult) and not result.succeeded:
            _record_failed_attempt(trigger, post_key, result.failure_cause)
        else:
            from ralph.git.merge import MergeResult

            if isinstance(result, MergeResult) and result.outcome != "success":
                _record_failed_attempt(trigger, post_key, result.failure_cause)
    return result


def _resolve_rebase_with_config(
    root: Path,
    target: str,
    resolver: RebaseStopResolver,
    config: ConflictResolutionConfig | None,
    agent_runner: AgentRunner | None = None,
) -> RebaseResolutionResult:
    from ralph.pipeline.conflict_resolution.session import ResolutionSession

    session = (
        ResolutionSession()
        if config is None
        else ResolutionSession(
            inactivity_timeout_seconds=config.inactivity_timeout_seconds,
            max_rounds_per_stop=config.max_rounds_per_stop,
            max_rebase_conflict_stops=config.max_rebase_conflict_stops,
            max_fallback_agents=config.max_fallback_agents,
            total_resolution_cap_seconds=config.total_resolution_cap_seconds,
        )
    )
    from ralph.pipeline import auto_integrate_rebase_merge as merge_module

    resolved = (
        agent_runner.resolve_rebase(root, target, resolver, session=session)
        if agent_runner is not None
        else merge_module.resolve_rebase_in_progress(root, target, resolver, session=session)
    )
    if resolved:
        return RebaseResolutionResult(
            True, None, ResolutionFailureCause.NONE, session.terminal_reason
        )
    carried_cause = session.last_failure_cause
    if carried_cause == ResolutionFailureCause.NONE:
        if session.terminal_reason == ResolutionTerminationReason.CONFIG_FAILURE:
            carried_cause = ResolutionFailureCause.CONFIG_FAILURE
        elif session.terminal_reason == ResolutionTerminationReason.CONTEXT_FAILURE:
            carried_cause = ResolutionFailureCause.CONTEXT_FAILURE
    if (
        not session.charge_conflict_budget
        or session.terminal_reason in RESOLVER_NOT_SPENT_TERMINATION_REASONS
    ):
        logger.warning(
            "auto_integrate: rebase conflict resolution for '{}' ended on {} without the "
            "resolver chain being spent; not recording it as exhausted",
            target,
            session.terminal_reason.value if session.terminal_reason else "no terminal evidence",
        )
        if session.terminal_reason == ResolutionTerminationReason.OUT_OF_REACH:
            reason = session.operator_action_message or session.exhaustion_reason
            if not reason and session.unresolved_paths:
                from ralph.pipeline.conflict_resolution.sight import gitlink_escalation_message

                reason = gitlink_escalation_message(session.unresolved_paths)
        elif session.terminal_reason == ResolutionTerminationReason.CANDIDATE_EXHAUSTED:
            reason = session.operator_action_message or session.exhaustion_reason
            if not reason or "needs operator action:" not in reason:
                from ralph.pipeline.conflict_resolution._operator_command import (
                    operator_command_for_paths,
                )

                paths = tuple(session.unresolved_paths)
                reason = (
                    "all resolver agents are spent/unavailable; conflict on "
                    f"{', '.join(paths)} needs operator action: {operator_command_for_paths(paths)}"
                )
        elif carried_cause in (
            ResolutionFailureCause.CONFIG_FAILURE,
            ResolutionFailureCause.CONTEXT_FAILURE,
        ):
            reason = session.exhaustion_reason or (
                session.terminal_reason.value if session.terminal_reason is not None else None
            )
        else:
            reason = None
        return RebaseResolutionResult(False, reason, carried_cause, session.terminal_reason)
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
    return RebaseResolutionResult(
        False,
        reason,
        carried_cause
        if carried_cause != ResolutionFailureCause.NONE
        else ResolutionFailureCause.UNRESOLVED_CONFLICT,
        session.terminal_reason,
    )
