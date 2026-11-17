from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

from loguru import logger

from ralph.git.merge import MERGE_STATE_IN_PROGRESS, MERGE_STATE_NONE, merge_state
from ralph.git.rebase.rebase_continuation import rebase_in_progress_at
from ralph.pipeline._commit_completion_authority import CommitCompletion, complete_commit
from ralph.pipeline._landed_predicate import operation_landed
from ralph.pipeline._pending_merge_commit import _git_value
from ralph.pipeline.agent_entry_point import (
    AgentEntryPoint,
    register_entry_point,
    registered_entry_points,
)
from ralph.pipeline.attempt_runner import run_agent_attempt
from ralph.pipeline.auto_integrate_record import (
    MARK_REINTEGRATE_PENDING,
    MARK_RESOLVING_REBASE,
    IntegrationRecord,
    read_record,
    write_record,
)
from ralph.pipeline.conflict_resolution import ResolutionTerminationReason
from ralph.pipeline.integration_attempt_ledger import AttemptLedger, rebase_resolution_signature
from ralph.pipeline.integration_decision import NextAction
from ralph.pipeline.rebase_state import StartupRetentionReason
from ralph.pipeline.resolution_outcome import (
    ConfigFailureOutcome,
    NextStrategyOutcome,
    ResolvedOutcome,
    RetryLaterOutcome,
    StopResolutionOutcome,
    UnknownOutcome,
    WaitingOutcome,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.attempt_result import AttemptResult
    from ralph.pipeline.attempt_runner_deps import AttemptRunnerDeps
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.recovery.controller import RecoveryController
    from ralph.workspace.scope import WorkspaceScope


def compute_continuation_retry_gate_key(
    root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None = None,
    *,
    recovery_controller: RecoveryController | None = None,
    phase: str = "rebase_conflict_resolution",
    chain_agents: list[str] | tuple[str, ...] | None = None,
    config_path: Path | None = None,
    stop_identity: str | None = None,
) -> object:
    from ralph.pipeline._stop_resolution_entry import compute_resolution_attempt_key

    return compute_resolution_attempt_key(
        root,
        record,
        config,
        recovery_controller=recovery_controller,
        phase=phase,
        chain_agents=None if chain_agents is None else tuple(chain_agents),
        config_path=config_path,
        stop_identity=stop_identity,
    )


def _continue_merge_resolution(
    root: Path,
    record: IntegrationRecord,
    target: str,
    config: UnifiedConfig | None,
    conflict_resolver: ConflictResolver | None,
    *,
    recovery_controller: RecoveryController | None,
    phase: str,
    chain_agents: list[str] | tuple[str, ...] | None,
    config_path: Path | None,
    force_retry: bool,
    stop_identity: str | None,
) -> str | None:
    matches = active_operation_matches(root, record)
    if not matches:
        return "active merge identity differs from retained ownership; operator work preserved"
    from ralph.pipeline._stop_resolution_entry import attempt_stop_resolution
    from ralph.pipeline.auto_integrate_resolve import resolve_and_commit_with_reason
    from ralph.pipeline.stop_resolution_blocked import StopResolutionBlocked
    from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger

    if conflict_resolver is None:
        key = compute_continuation_retry_gate_key(
            root,
            record,
            config,
            recovery_controller=recovery_controller,
            phase=phase,
            chain_agents=chain_agents,
            config_path=config_path,
            stop_identity=stop_identity,
        )
        from ralph.pipeline.resolution_attempt_key import ResolutionAttemptKey

        if isinstance(key, ResolutionAttemptKey):
            stored = (key.stop_identity, key.eligible_agents, key.config_fingerprint)
            write_record(root, (read_record(root) or record).with_resolution_gate_key(stored))
        return "merge resolution retained; continuation resolver required"
    result = attempt_stop_resolution(
        stop_identity,
        StopResolutionTrigger(
            name="merge_stop",
            root=root,
            target=target,
            resolver=conflict_resolver,
            record=record,
            config=config,
            recovery_controller=recovery_controller,
            phase=phase,
            chain_agents=None if chain_agents is None else tuple(chain_agents),
            config_path=config_path,
            force_retry=force_retry,
            launcher=lambda: resolve_and_commit_with_reason(root, target, conflict_resolver),
        ),
    )
    if isinstance(result, StopResolutionBlocked):
        return result.reason
    from ralph.git.merge import MergeResult

    if not isinstance(result, MergeResult):
        return "merge resolution returned an invalid result"
    if result.outcome != "success":
        return result.reason or "merge resolution remains pending after continuation"
    return None


def _continue_rebase_resolution(
    root: Path,
    record: IntegrationRecord,
    target: str,
    config: UnifiedConfig | None,
    rebase_stop_resolver: RebaseStopResolver | None,
    *,
    recovery_controller: RecoveryController | None,
    phase: str,
    chain_agents: list[str] | tuple[str, ...] | None,
    config_path: Path | None,
    force_retry: bool,
    stop_identity: str | None,
) -> str | RetryLaterOutcome | None:
    matches = active_operation_matches(root, record)
    if not matches:
        return "active rebase identity differs from retained ownership; operator work preserved"
    if rebase_stop_resolver is None:
        return _retain_rebase_without_resolver(
            root,
            record,
            target,
            config,
            recovery_controller,
            phase,
            chain_agents,
            config_path,
            force_retry,
            stop_identity,
        )
    from ralph.pipeline._stop_resolution_entry import attempt_stop_resolution
    from ralph.pipeline.stop_resolution_blocked import StopResolutionBlocked
    from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger

    result = attempt_stop_resolution(
        stop_identity,
        StopResolutionTrigger(
            name="rebase_stop",
            root=root,
            target=target,
            resolver=rebase_stop_resolver,
            record=record,
            config=config,
            conflict_config=config.conflict_resolution if config is not None else None,
            recovery_controller=recovery_controller,
            phase=phase,
            chain_agents=None if chain_agents is None else tuple(chain_agents),
            config_path=config_path,
            force_retry=force_retry,
        ),
    )
    if isinstance(result, StopResolutionBlocked):
        return result.reason
    from ralph.pipeline.rebase_resolution_result import RebaseResolutionResult

    outcome_to_check = result.outcome if isinstance(result, RebaseResolutionResult) else result
    if not isinstance(
        outcome_to_check,
        (
            ResolvedOutcome,
            WaitingOutcome,
            RetryLaterOutcome,
            NextStrategyOutcome,
            ConfigFailureOutcome,
            UnknownOutcome,
        ),
    ):
        return "rebase resolution returned an invalid result"
    return _rebase_failure_transport(
        root,
        record,
        stop_identity,
        outcome_to_check,
        tuple(chain_agents or ()),
    )


def _rebase_failure_outcome(
    result: object,
    chain_agents: tuple[str, ...] = (),
) -> StopResolutionOutcome:
    del chain_agents
    if isinstance(result, ResolvedOutcome):
        return ResolvedOutcome()
    if isinstance(
        result,
        (
            WaitingOutcome,
            RetryLaterOutcome,
            NextStrategyOutcome,
            ConfigFailureOutcome,
            UnknownOutcome,
        ),
    ):
        return result
    return UnknownOutcome(RuntimeError(f"unexpected outcome {result!r}"))


def _rebase_failure_transport(
    root: Path,
    record: IntegrationRecord,
    stop_identity: str | None,
    result: StopResolutionOutcome,
    chain_agents: tuple[str, ...],
) -> str | RetryLaterOutcome | None:
    failure_reason = _continuation_outcome_reason(result, record.last_failure_cause.value)
    outcome = _rebase_failure_outcome(result)
    match outcome:
        case ResolvedOutcome():
            return None
        case NextStrategyOutcome(strategy=strategy):
            from ralph.pipeline._integration_ladder_evidence import _LADDER_ACTION_VALUES

            if strategy in _LADDER_ACTION_VALUES:
                AttemptLedger(root).charge_ladder_attempt(
                    rebase_resolution_signature(
                        record.pre_feature_sha,
                        record.pre_target_sha,
                        stop_identity or record.resolving_stop_sha,
                    ),
                    strategy,
                )
            return failure_reason
        case RetryLaterOutcome(
            terminal_reason=ResolutionTerminationReason.CANDIDATE_EXHAUSTED,
            on_change=on_change,
            failure_cause=failure_cause,
            unresolved_paths=unresolved_paths,
        ):
            AttemptLedger(root).charge_ladder_attempt(
                rebase_resolution_signature(
                    record.pre_feature_sha,
                    record.pre_target_sha,
                    stop_identity or record.resolving_stop_sha,
                ),
                NextAction.LAST_RESORT_RECOVERY.value,
            )
            msg = on_change or (
                failure_reason
                if "spent/unavailable" in failure_reason
                else f"all resolver agents are spent/unavailable; {failure_reason}"
            )
            return RetryLaterOutcome(
                on_change=msg,
                terminal_reason=ResolutionTerminationReason.CANDIDATE_EXHAUSTED,
                unresolved_paths=unresolved_paths,
                failure_cause=failure_cause,
            )
        case WaitingOutcome() | RetryLaterOutcome():
            return failure_reason
        case ConfigFailureOutcome():
            logger.warning("{}", failure_reason)
            return failure_reason
        case UnknownOutcome():
            return failure_reason
        case _ as unreachable:
            assert_never(unreachable)


def _continuation_outcome_reason(outcome: StopResolutionOutcome, cause: str) -> str:
    match outcome:
        case ResolvedOutcome():
            return "conflict resolution completed"
        case WaitingOutcome(external_condition=condition, resumes_when=resumes_when):
            return f"{condition}; resumes when {resumes_when}"
        case RetryLaterOutcome(until=until, on_change=on_change):
            return on_change or f"retry after {until}"
        case NextStrategyOutcome(strategy=strategy):
            return f"continue with autonomous strategy: {strategy}"
        case ConfigFailureOutcome(file=file):
            return f"fix {file} then run ralph --check-config"
        case UnknownOutcome(error=error):
            return str(error) or f"rebase resolution remains pending (cause: {cause})"
        case _ as unreachable:
            assert_never(unreachable)


def _retain_rebase_without_resolver(
    root: Path,
    record: IntegrationRecord,
    target: str,
    config: UnifiedConfig | None,
    recovery_controller: RecoveryController | None,
    phase: str,
    chain_agents: list[str] | tuple[str, ...] | None,
    config_path: Path | None,
    force_retry: bool,
    stop_identity: str | None,
) -> str:
    from ralph.pipeline._stop_resolution_entry import attempt_stop_resolution
    from ralph.pipeline.resolution_attempt_key import ResolutionAttemptKey
    from ralph.pipeline.stop_resolution_blocked import StopResolutionBlocked
    from ralph.pipeline.stop_resolution_trigger import StopResolutionTrigger

    blocked = attempt_stop_resolution(
        stop_identity,
        StopResolutionTrigger(
            name="rebase_stop",
            root=root,
            target=target,
            resolver=object(),
            record=record,
            config=config,
            recovery_controller=recovery_controller,
            phase=phase,
            chain_agents=None if chain_agents is None else tuple(chain_agents),
            config_path=config_path,
            force_retry=force_retry,
            launcher=lambda: True,
        ),
    )
    if isinstance(blocked, StopResolutionBlocked):
        return blocked.reason
    key = compute_continuation_retry_gate_key(
        root,
        record,
        config,
        recovery_controller=recovery_controller,
        phase=phase,
        chain_agents=chain_agents,
        config_path=config_path,
        stop_identity=stop_identity,
    )
    if isinstance(key, ResolutionAttemptKey):
        stored = (key.stop_identity, key.eligible_agents, key.config_fingerprint)
        write_record(root, (read_record(root) or record).with_resolution_gate_key(stored))
    return "rebase resolution retained; continuation resolver required"


def continue_retained_resolution(
    root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None,
    conflict_resolver: ConflictResolver | None,
    rebase_stop_resolver: RebaseStopResolver | None,
    *,
    recovery_controller: RecoveryController | None = None,
    phase: str = "rebase_conflict_resolution",
    chain_agents: list[str] | tuple[str, ...] | None = None,
    config_path: Path | None = None,
    force_retry: bool = False,
    stop_identity: str | None = None,
) -> IntegrationRecord | str | RetryLaterOutcome:
    from ralph.pipeline._integration_recovery_controller import shared_recovery_controller
    from ralph.pipeline.effect_executor import current_agent_effect_dependencies

    if recovery_controller is None:
        recovery_controller = current_agent_effect_dependencies().recovery_controller
    if recovery_controller is not None:
        recovery_controller.set_phase_launch_gate(
            phase,
            available=(
                conflict_resolver is not None
                if record.resolving_merge
                else rebase_stop_resolver is not None
            ),
        )
    with shared_recovery_controller(recovery_controller):
        return _run_retained_resolution(
            root,
            record,
            config,
            conflict_resolver,
            rebase_stop_resolver,
            recovery_controller=recovery_controller,
            phase=phase,
            chain_agents=chain_agents,
            config_path=config_path,
            force_retry=force_retry,
            stop_identity=stop_identity,
        )


def _run_retained_resolution(
    root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None,
    conflict_resolver: ConflictResolver | None,
    rebase_stop_resolver: RebaseStopResolver | None,
    *,
    recovery_controller: RecoveryController | None,
    phase: str,
    chain_agents: list[str] | tuple[str, ...] | None,
    config_path: Path | None,
    force_retry: bool,
    stop_identity: str | None,
) -> IntegrationRecord | str | RetryLaterOutcome:
    target = record.pre_target_sha if record.operation_kind == "target_reconcile" else record.target
    if not target:
        return "retained resolution target unreadable; progress preserved"
    if (record.resolving_merge or merge_state(root) == MERGE_STATE_IN_PROGRESS) and merge_state(
        root
    ) == MERGE_STATE_IN_PROGRESS:
        failure: str | RetryLaterOutcome | None = _continue_merge_resolution(
            root,
            record,
            target,
            config,
            conflict_resolver,
            recovery_controller=recovery_controller,
            phase=phase,
            chain_agents=chain_agents,
            config_path=config_path,
            force_retry=force_retry,
            stop_identity=stop_identity,
        )
        if failure is not None:
            return failure
    elif (record.resolving_rebase or rebase_in_progress_at(root)) and rebase_in_progress_at(root):
        rebase_failure = _continue_rebase_resolution(
            root,
            record,
            target,
            config,
            rebase_stop_resolver,
            recovery_controller=recovery_controller,
            phase=phase,
            chain_agents=chain_agents,
            config_path=config_path,
            force_retry=force_retry,
            stop_identity=stop_identity,
        )
        if rebase_failure is not None:
            return rebase_failure
    return _promote_completed_resolution(root, record)


def active_operation_matches(root: Path, record: IntegrationRecord) -> bool:
    from ralph.pipeline.conflict_resolution.rebase_loop import current_rebase_identity

    if rebase_in_progress_at(root):
        return current_rebase_identity(root) == (record.pre_feature_sha, record.pre_target_sha)
    return (
        merge_state(root) == MERGE_STATE_IN_PROGRESS
        and _git_value(root, "rev-parse", "--verify", "HEAD") == record.pre_feature_sha
        and _git_value(root, "rev-parse", "--verify", "MERGE_HEAD") == record.pre_target_sha
    )


def _terminal_state_refusal(root: Path) -> str | None:
    from ralph.pipeline.auto_integrate_recovery_terminal import (
        TerminalStateViolationError,
        post_attempt_verify,
    )

    try:
        post_attempt_verify(root, expected_head_sha=None, owns_resolution=False)
    except TerminalStateViolationError as exc:
        return (
            f"retained integration completion unproven; Git still shows an operation marker: {exc}"
        )
    return None


def _complete_and_save_record(
    root: Path,
    record: IntegrationRecord,
    head: str,
) -> IntegrationRecord:
    completion = complete_commit(
        record,
        integrated_feature_sha=head,
        resolving_rebase=False,
        resolving_merge=False,
        rebase_continue_pending=False,
        rebase_continue_metadata=(),
        rebase_restore_metadata=None,
        rebase_continue_head=None,
        rebase_continue_stop=None,
        rebase_continue_tree=None,
        rebase_continue_error=None,
        merge_commit_pending=False,
    )
    write_record(root, completion.record)
    return completion.record


def _promote_completed_resolution(root: Path, record: IntegrationRecord) -> IntegrationRecord | str:
    refusal = _terminal_state_refusal(root)
    if refusal is None and (rebase_in_progress_at(root) or merge_state(root) != MERGE_STATE_NONE):
        refusal = "retained integration operation remains active; continuation required"
    if refusal is not None:
        return refusal
    current = read_record(root)
    if current is None:
        return "retained integration ownership unreadable after continuation"
    if current.is_integrated:
        return current
    landing_record = (
        current
        if current.rebase_continue_stop or not current.resolving_stop_sha
        else current.model_copy(update={"rebase_continue_stop": current.resolving_stop_sha})
    )
    decision = operation_landed(root, landing_record)
    if decision.landed and decision.observed_head is not None:
        return _complete_and_save_record(root, current, decision.observed_head)
    if current.rebase_continue_pending:
        from ralph.pipeline._pending_rebase_continue import resume_pending_rebase

        return resume_pending_rebase(root, current)
    replay = _stale_replay_record(root, current)
    return current if replay is None else replay


promote_completed_resolution = _promote_completed_resolution


def _stale_replay_record(root: Path, record: IntegrationRecord) -> IntegrationRecord | None:
    from ralph.pipeline._owned_landing_reintegration import retained_resolution_is_stale

    if not (
        record.operation_kind == "feature_integrate"
        and (record.resolving_rebase or record.resolving_merge)
        and retained_resolution_is_stale(root, record)
    ):
        return None
    replay = record.with_transition(
        MARK_RESOLVING_REBASE,
        resolving_rebase=False,
        resolving_merge=False,
    ).with_transition(
        MARK_REINTEGRATE_PENDING,
        reintegrate_pending=True,
        owning_worktree=record.owning_worktree or str(root),
    )
    write_record(root, replay)
    return replay


def _legacy_completed_rebase(root: Path, record: IntegrationRecord) -> CommitCompletion | None:
    if rebase_in_progress_at(root):
        return None
    if not record.resolving_rebase and not record.rebase_continue_pending:
        return None
    if rebase_in_progress_at(root) or merge_state(root) != MERGE_STATE_NONE:
        return None
    landing_record = (
        record
        if record.rebase_continue_stop or not record.resolving_stop_sha
        else record.model_copy(update={"rebase_continue_stop": record.resolving_stop_sha})
    )
    decision = operation_landed(root, landing_record)
    if not decision.landed or decision.observed_head is None:
        return None
    return complete_commit(
        record.with_transition(MARK_RESOLVING_REBASE, resolving_rebase=False),
        integrated_feature_sha=decision.observed_head,
        resolving_rebase=False,
        resolving_merge=False,
        rebase_continue_pending=False,
        rebase_continue_metadata=(),
        rebase_restore_metadata=None,
        rebase_continue_head=None,
        rebase_continue_stop=None,
        rebase_continue_tree=None,
        rebase_continue_error=None,
        merge_commit_pending=False,
    )


def recover_before_attempt(
    config: UnifiedConfig,
    scope: WorkspaceScope,
    prior_attempt: RebaseState | None,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    *,
    recovery_controller: RecoveryController | None = None,
) -> RebaseState | None:
    from ralph.pipeline.auto_integrate_recovery import (
        recover_incomplete_integration,
        recovery_retained_record,
    )
    from ralph.pipeline.integration_resolution import retained_integration_reason

    if recovery_controller is None:
        from ralph.pipeline.effect_executor import current_agent_effect_dependencies

        recovery_controller = current_agent_effect_dependencies().recovery_controller
    if retained_integration_reason(scope.root) is None:
        return None
    recovered = recover_incomplete_integration(
        scope,
        config=config,
        conflict_resolver=conflict_resolver,
        rebase_stop_resolver=rebase_stop_resolver,
        recovery_controller=recovery_controller,
    )
    if recovery_retained_record(recovered):
        if prior_attempt is not None:
            return prior_attempt.set_recovery_record_retained(
                StartupRetentionReason.OBSERVED_RETAINED_ON_DISK
            )
        return recovered
    if recovered is not None and recovered.fast_forwarded:
        return recovered
    if not config.general.auto_integrate_enabled and recovered is not None:
        from ralph.pipeline.auto_integrate import auto_integrate_after_commit

        return auto_integrate_after_commit(
            config_for_owned_integration(config),
            scope,
            recovered,
            conflict_resolver=conflict_resolver,
            rebase_stop_resolver=rebase_stop_resolver,
            recovery_controller=recovery_controller,
        )
    return None


def config_for_owned_integration(config: UnifiedConfig) -> UnifiedConfig:
    return (
        config
        if config.general.auto_integrate_enabled
        else config.model_copy(
            update={
                "general": config.general.model_copy(update={"auto_integrate_enabled": True}),
            }
        )
    )


def _run_integration_continuation_attempt(
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
        AgentEntryPoint.INTEGRATION_CONTINUATION,
        effect=effect,
        invocation=invocation,
        config=config,
        controller=controller,
        deps=deps,
        state=state,
        **dict(execute_kwargs),
    )


if AgentEntryPoint.INTEGRATION_CONTINUATION not in registered_entry_points():
    register_entry_point(
        AgentEntryPoint.INTEGRATION_CONTINUATION,
        _run_integration_continuation_attempt,
    )
