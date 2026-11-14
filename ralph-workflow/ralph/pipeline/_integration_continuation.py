from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

from ralph.git.merge import MERGE_STATE_IN_PROGRESS, MERGE_STATE_NONE, is_ancestor, merge_state
from ralph.git.rebase.rebase_continuation import rebase_in_progress_at
from ralph.git.subprocess_runner import run_git
from ralph.pipeline._commit_completion_authority import CommitCompletion, complete_commit
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


_REBASE_RECEIPT_ENTRIES = 2


class ExhaustedResolutionFailure(str):
    terminal_reason: ResolutionTerminationReason

    def __new__(
        cls,
        reason: str,
        terminal_reason: ResolutionTerminationReason,
    ) -> ExhaustedResolutionFailure:
        value = super().__new__(cls, reason)
        value.terminal_reason = terminal_reason
        return value


def reset_continuation_gate_state() -> None:
    from ralph.pipeline._stop_resolution_entry import reset_stop_resolution_presentation

    reset_stop_resolution_presentation()


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
) -> str | None:
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
    if not isinstance(
        result,
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
        result,
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
) -> str | None:
    failure_reason = _continuation_outcome_reason(result, record.last_failure_cause.value)
    outcome = _rebase_failure_outcome(result)
    match outcome:
        case ResolvedOutcome():
            return None
        case WaitingOutcome() | RetryLaterOutcome():
            return failure_reason
        case NextStrategyOutcome():
            AttemptLedger(root).charge_ladder_attempt(
                rebase_resolution_signature(
                    record.pre_feature_sha,
                    record.pre_target_sha,
                    stop_identity or record.resolving_stop_sha,
                ),
                NextAction.LAST_RESORT_RECOVERY.value,
            )
            msg = (
                failure_reason
                if "spent/unavailable" in failure_reason
                else f"all resolver agents are spent/unavailable; {failure_reason}"
            )
            return ExhaustedResolutionFailure(
                msg,
                ResolutionTerminationReason.CANDIDATE_EXHAUSTED,
            )
        case ConfigFailureOutcome() | UnknownOutcome():
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
) -> IntegrationRecord | str:
    if recovery_controller is not None:
        recovery_controller.set_phase_launch_gate(
            phase,
            available=rebase_stop_resolver is not None and conflict_resolver is not None,
        )
    target = record.pre_target_sha if record.operation_kind == "target_reconcile" else record.target
    if not target:
        return "retained resolution target unreadable; progress preserved"
    if record.resolving_merge and merge_state(root) == MERGE_STATE_IN_PROGRESS:
        failure = _continue_merge_resolution(
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
    elif record.resolving_rebase and rebase_in_progress_at(root):
        failure = _continue_rebase_resolution(
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
        if failure is not None:
            return failure
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
    if current.rebase_continue_pending:
        from ralph.pipeline._pending_rebase_continue import resume_pending_rebase

        return resume_pending_rebase(root, current)
    legacy = _legacy_completed_rebase(root, current)
    if legacy is not None:
        write_record(root, legacy.record)
        return legacy.record
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
    if not record.resolving_rebase or not record.pre_target_sha:
        return None
    branch = _git_value(root, "symbolic-ref", "--quiet", "HEAD")
    head = _git_value(root, "rev-parse", "--verify", "HEAD")
    if branch is None or head is None:
        return None
    history = run_git(
        ("reflog", "show", "--format=%H%x00%gs", "-2", branch),
        cwd=root,
        label="recovery:legacy-rebase-receipt",
    )
    rows = history.stdout.splitlines()
    if history.returncode or len(rows) != _REBASE_RECEIPT_ENTRIES:
        return None
    latest, _, action = rows[0].partition("\0")
    previous, _, _action = rows[1].partition("\0")
    if (
        latest != head
        or previous != record.pre_feature_sha
        or action != f"rebase (finish): {branch} onto {record.pre_target_sha}"
        or not is_ancestor(root, record.pre_target_sha, head)
    ):
        return None
    return complete_commit(
        record.with_transition(MARK_RESOLVING_REBASE, resolving_rebase=False),
        integrated_feature_sha=head,
    )


def recover_before_attempt(
    config: UnifiedConfig,
    scope: WorkspaceScope,
    prior_attempt: RebaseState | None,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
) -> RebaseState | None:
    from ralph.pipeline.auto_integrate_recovery import (
        recover_incomplete_integration,
        recovery_retained_record,
    )
    from ralph.pipeline.integration_resolution import retained_integration_reason

    if retained_integration_reason(scope.root) is None:
        return None
    recovered = recover_incomplete_integration(
        scope,
        config=config,
        conflict_resolver=conflict_resolver,
        rebase_stop_resolver=rebase_stop_resolver,
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
        **execute_kwargs,
    )


if AgentEntryPoint.INTEGRATION_CONTINUATION not in registered_entry_points():
    register_entry_point(
        AgentEntryPoint.INTEGRATION_CONTINUATION,
        _run_integration_continuation_attempt,
    )
