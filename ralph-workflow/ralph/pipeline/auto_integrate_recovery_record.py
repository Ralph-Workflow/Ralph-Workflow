from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

from loguru import logger

from ralph.git.merge import (
    MERGE_STATE_IN_PROGRESS,
    MERGE_STATE_NONE,
    observe_unmerged_paths,
)
from ralph.git.merge_obstructions import ancestry_state
from ralph.git.operations import get_head_sha
from ralph.git.rebase.rebase_continuation import rebase_in_progress_at
from ralph.git.subprocess_runner import run_git
from ralph.pipeline._git_operation_authority import (
    GitOperationKind,
    classify_git_operation,
    is_in_active_resolution_state,
)
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline._integration_continuation import (
    compute_continuation_retry_gate_key,
    reset_continuation_gate_state,
)
from ralph.pipeline._retry_later_outcome import RetryLaterOutcome
from ralph.pipeline._integration_continuation import (
    continue_retained_resolution as _continue_retained_resolution,
)
from ralph.pipeline._integration_log_dedupe import integration_outcome_log
from ralph.pipeline._owned_landing_reintegration import (
    reintegrate_owned_landing as _reintegrate_owned_landing,
)
from ralph.pipeline._owned_landing_reintegration import (
    replay_stale_resolution as _replay_stale_resolution,
)
from ralph.pipeline._owned_landing_reintegration import (
    retained_resolution_is_stale as _retained_resolution_is_stale,
)
from ralph.pipeline._pending_merge_commit import resume_pending_merge
from ralph.pipeline._pending_rebase_continue import resume_pending_rebase
from ralph.pipeline._recovery_fast_forward_landing import (
    _record_skip,
)
from ralph.pipeline._terminal_emitter import critical
from ralph.pipeline.auto_integrate_context import (
    record_refresh,
    refresh_outcome_is_healthy,
)
from ralph.pipeline.auto_integrate_record import (
    MARK_REINTEGRATE_PENDING,
    RELEASE_DIAGNOSTIC_EVIDENCE,
    IntegrationRecord,
    RecordBusyError,
    RecordReadOutcomeDisposition,
    RecordUnreadableError,
    _read_record_raw,
    read_record_outcome,
    record_path,
    write_record,
)
from ralph.pipeline.auto_integrate_sync import REFRESH_UNREACHABLE
from ralph.pipeline.conflict_resolution import ResolutionTerminationReason
from ralph.pipeline.integration_observation import InProgressKind, read_in_progress_kind
from ralph.pipeline.rebase_state import RebaseState, StartupRetentionReason

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.mcp.artifacts.file_backend import FileBackend
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.recovery.controller import RecoveryController
    from ralph.workspace.scope import WorkspaceScope


__all__ = [
    "_continue_fast_forward_from_record",
    "_maybe_release_diagnostic",
    "_read_outcome_or_skip",
    "_recover_pending_merge",
    "_recover_unresolved_integrating",
    "_refresh_before_verdict",
    "compute_continuation_retry_gate_key",
    "continue_retained_resolution",
    "recover_pending_merge",
    "reset_continuation_gate_state",
]

_REBASE_RECEIPT_ENTRIES = 2


def _completed_rebase_receipt_head(
    root: Path,
    record: IntegrationRecord,
) -> str | None:
    if not record.resolving_rebase or not record.pre_target_sha:
        return None
    if (
        rebase_in_progress_at(root)
        or classify_git_operation(record) is not GitOperationKind.RESOLVING_REBASE
    ):
        return None
    branch = run_git(
        ("symbolic-ref", "--quiet", "HEAD"),
        cwd=root,
        label="recovery:completed-rebase-branch",
    )
    head = _read_head_sha_or_none(root)
    if branch.returncode != 0 or not branch.stdout.strip() or head is None:
        return None
    branch_ref = branch.stdout.strip()
    history = run_git(
        ("reflog", "show", "--format=%H%x00%gs", "-2", branch_ref),
        cwd=root,
        label="recovery:completed-rebase-receipt",
    )
    rows = history.stdout.splitlines()
    if history.returncode != 0 or len(rows) != _REBASE_RECEIPT_ENTRIES:
        return None
    latest, separator, action = rows[0].partition("\0")
    previous, previous_separator, _ = rows[1].partition("\0")
    if (
        not separator
        or not previous_separator
        or latest != head
        or previous != record.pre_feature_sha
        or action != f"rebase (finish): {branch_ref} onto {record.pre_target_sha}"
        or ancestry_state(root, record.pre_target_sha, head) is not True
    ):
        return None
    return head


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
    completed_head = _completed_rebase_receipt_head(root, record)
    if completed_head is not None:
        completed = record.with_completion_advanced(completed_head)
        write_record(root, completed)
        return completed
    effective_controller = recovery_controller
    if effective_controller is None:
        from ralph.pipeline.effect_executor import current_agent_effect_dependencies

        effective_controller = current_agent_effect_dependencies().recovery_controller
    if effective_controller is None:
        from ralph.agents.system_clock import SystemClock
        from ralph.pipeline.agent_entry_point import AgentEntryPoint
        from ralph.pipeline.attempt_runner import build_entry_point_recovery_controller
        from ralph.pipeline.attempt_runner_deps import PRODUCTION_INVOKE, AttemptRunnerDeps
        from ralph.pipeline.runner import load_policy_bundle_for_run
        from ralph.workspace.scope import WorkspaceScope

        try:
            policy_bundle = (
                load_policy_bundle_for_run(WorkspaceScope(root=root), config)
                if config is not None
                else None
            )
        except (OSError, RuntimeError, ValueError, KeyError):
            policy_bundle = None
        effective_controller = build_entry_point_recovery_controller(
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
    return _continue_retained_resolution(
        root,
        record,
        config,
        conflict_resolver,
        rebase_stop_resolver,
        recovery_controller=effective_controller,
        phase=phase,
        chain_agents=chain_agents,
        config_path=config_path,
        force_retry=force_retry,
        stop_identity=stop_identity,
    )


def _refresh_before_verdict(
    config: UnifiedConfig | None,
    workspace_root: Path,
    target: str,
) -> tuple[str | None, bool]:
    if config is None:
        return None, True
    import ralph.pipeline.auto_integrate_recovery as _rec_mod

    refresh_fn = _rec_mod._refresh_target
    try:
        outcome = refresh_fn(config, workspace_root, target)
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        logger.warning("recovery: target refresh raised: {}", exc)
        return REFRESH_UNREACHABLE, False
    return outcome, refresh_outcome_is_healthy(outcome)


def _continue_fast_forward_from_record(
    workspace_root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None = None,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
) -> RebaseState:
    feature_sha = record.integrated_feature_sha
    if feature_sha is None or not _head_matches_sha(workspace_root, feature_sha):
        return _withheld_landing(workspace_root, record, config)
    refresh, pointer_is_fresh = _refresh_before_verdict(config, workspace_root, record.target)
    if not pointer_is_fresh:
        return record_refresh(
            _record_skip(
                reason=(
                    "recovery: target pointer could not be refreshed, record retained for retry"
                ),
                target=record.target,
                retention_reason=StartupRetentionReason.OWNED_TARGET_PRECONDITION_FAILED,
                record_retained=True,
            ),
            refresh,
        )
    if _get_branch_sha(workspace_root, record.target) == feature_sha:
        _clear_record(workspace_root)
        from ralph.pipeline.auto_integrate_remote_sync import reconcile_remote_publication

        return record_refresh(
            reconcile_remote_publication(
                config,
                workspace_root,
                record.target,
                source_state=RebaseState(
                    last_outcome=RebaseOutcomeKind.RECOVERED,
                    last_reason="already fast-forwarded",
                    last_target=record.target,
                    fast_forwarded=True,
                ),
            ),
            refresh,
        )
    import ralph.pipeline.auto_integrate_recovery as _rec_mod

    ancestry = _rec_mod.ancestry_state(workspace_root, record.target, feature_sha)
    if ancestry is None:
        return record_refresh(
            _record_skip(
                reason="recovery: target ancestry unreadable; record retained for retry",
                target=record.target,
                retention_reason=StartupRetentionReason.TARGET_POINTER_UNREFRESHABLE,
                record_retained=True,
            ),
            refresh,
        )
    if not ancestry:
        return _reintegrate_owned_landing(
            workspace_root,
            record,
            config,
            conflict_resolver,
            rebase_stop_resolver,
        )
    return _rec_mod._land_and_reconcile(workspace_root, record, feature_sha, refresh, config)


def _withheld_landing(
    workspace_root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None,
) -> RebaseState:
    if record.integrated_feature_sha is None:
        reason = "recovery: malformed integrated record; verified feature SHA missing"
    elif (
        record.reintegrate_pending
        and _recorded_work_is_verifiably_landed(workspace_root, record)
        and _target_tip_contains_recorded_work(workspace_root, record)
    ):
        return _release_verifiably_landed_record(workspace_root, record, config)
    else:
        reason = "recovery: feature tip changed or unreadable; landing withheld"
    return _record_skip(
        reason=reason,
        target=record.target,
        retention_reason=StartupRetentionReason.OWNED_TARGET_PRECONDITION_FAILED,
        record_retained=True,
    )


def _head_matches_sha(workspace_root: Path, expected_sha: str) -> bool:
    import ralph.pipeline.auto_integrate_recovery as _rec_mod

    return bool(_rec_mod._head_matches_sha(workspace_root, expected_sha))


def _clear_record(workspace_root: Path) -> None:
    import ralph.pipeline.auto_integrate_recovery as _rec_mod

    _rec_mod._clear_record(workspace_root)


def _get_branch_sha(workspace_root: Path, target: str) -> str | None:
    import ralph.pipeline.auto_integrate_recovery as _rec_mod

    return _rec_mod.branch_sha(workspace_root, target)


def _read_outcome_or_skip(root: Path, backend: FileBackend) -> RebaseState | None:
    try:
        outcome = read_record_outcome(root, backend=backend)
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        critical("Integration ownership unreadable; recovery retained: {}", exc)
        return _record_skip(
            reason=f"integration ownership unreadable: {exc}",
            target=None,
            retention_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
            record_retained=True,
        )
    if outcome.disposition is RecordReadOutcomeDisposition.UNREADABLE:
        reason = (
            "durable integration record unreadable; escalating through the "
            "autonomous fallback ladder; record retained, bytes preserved"
        ) + (f" ({outcome.unreadable_reason})" if outcome.unreadable_reason else "")
        return RebaseState(
            last_outcome=RebaseOutcomeKind.LADDER_ESCALATION,
            last_reason=reason,
            last_target=None,
            recovery_record_retained=True,
            recovery_record_retained_reason=(StartupRetentionReason.OBSERVED_RETAINED_ON_DISK),
        )
    return None


def _apply_release_or_typed_skip(
    root: Path,
    record: IntegrationRecord,
    backend: FileBackend,
) -> IntegrationRecord | RebaseState | None:
    try:
        import ralph.pipeline.auto_integrate_recovery as _rec_mod

        _rec_mod.apply_record_transition(
            root,
            RELEASE_DIAGNOSTIC_EVIDENCE,
            backend=backend,
            diagnostic_evidence=(),
            diagnostic_ownership=None,
            diagnostic_owner_pid=None,
            diagnostic_owner_boot_id=None,
            diagnostic_intervention_required=False,
            repair_cap_reason=None,
        )
    except RecordBusyError:
        return _record_skip(
            reason="integration record busy; recovery will retry",
            target=record.target,
            retention_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
            record_retained=True,
        )
    except RecordUnreadableError:
        return RebaseState(
            last_outcome=RebaseOutcomeKind.LADDER_ESCALATION,
            last_reason=(
                "durable integration record unreadable at release; escalating through "
                "the autonomous fallback ladder; record retained, bytes preserved"
            ),
            last_target=record.target,
            recovery_record_retained=True,
            recovery_record_retained_reason=(StartupRetentionReason.OBSERVED_RETAINED_ON_DISK),
        )
    return read_record_outcome(root, backend=backend).record


def _maybe_release_diagnostic(
    root: Path,
    workspace_scope: WorkspaceScope,
    backend: FileBackend,
) -> IntegrationRecord | RebaseState | None:
    outcome = read_record_outcome(root, backend=backend)
    record = outcome.record
    if record is None or record.operation_kind == "target_reconcile":
        return record
    if not record.diagnostic_evidence and not record.diagnostic_ownership:
        from ralph.pipeline._integration_diagnostic import DIAGNOSTIC_REPAIR_ATTEMPT_CAP

        if (
            record.diagnostic_intervention_required
            or record.merge_commit_repair_attempts >= DIAGNOSTIC_REPAIR_ATTEMPT_CAP
        ):
            return _apply_release_or_typed_skip(root, record, backend)
        raw_payload = _read_record_raw(record_path(root), backend=backend)
        if (
            isinstance(raw_payload, dict)
            and raw_payload.get("diagnostic_intervention_required") is True
        ):
            return _apply_release_or_typed_skip(root, record, backend)
        return record
    from ralph.pipeline._diagnostic_evidence_gate import diagnostic_evidence_gate_reason
    from ralph.pipeline._integration_diagnostic import (
        _operation_root_for,
        release_verified_diagnostic,
    )
    from ralph.pipeline.operation_evidence import EvidenceVerdict

    verdict = release_verified_diagnostic(_operation_root_for(record, workspace_scope.root), record)
    match verdict:
        case EvidenceVerdict.UNCHANGED | EvidenceVerdict.UNKNOWN:
            return _apply_release_or_typed_skip(root, record, backend)
        case EvidenceVerdict.CHANGED:
            return _record_skip(
                reason=diagnostic_evidence_gate_reason(verdict),
                target=record.target,
                retention_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
                record_retained=True,
            )
        case _ as unreachable:
            assert_never(unreachable)


def _recover_unresolved_integrating(
    root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None,
    conflict_resolver: ConflictResolver | None,
    rebase_stop_resolver: RebaseStopResolver | None,
    force_retry: bool = False,
) -> RebaseState:
    import ralph.pipeline.auto_integrate_recovery as _rec_mod

    if _rec_mod.merge_state(root) not in {MERGE_STATE_NONE, MERGE_STATE_IN_PROGRESS}:
        return _record_skip(
            reason="recovery: merge state unreadable; record retained for retry",
            target=record.target,
            retention_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
            record_retained=True,
        )
    if _retained_resolution_is_stale(root, record) and _recorded_identity_moved(root, record):
        return _replay_stale_resolution(
            root,
            record,
            config,
            conflict_resolver,
            rebase_stop_resolver,
        )
    return _reintegrate_owned_landing(
        root,
        record,
        config,
        conflict_resolver,
        rebase_stop_resolver,
        force_retry=force_retry,
    )


def _recorded_identity_moved(root: Path, record: IntegrationRecord) -> bool:
    try:
        import ralph.git.operations as _git_ops

        head = _git_ops.get_head_sha(root)
    except (OSError, RuntimeError, ValueError, KeyError):
        return True
    return head != record.pre_feature_sha


def _recover_pending_merge_preflight(
    root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None,
    conflict_resolver: ConflictResolver | None,
    rebase_stop_resolver: RebaseStopResolver | None,
) -> RebaseState | None:
    if record.operation_kind == "target_reconcile":
        from ralph.pipeline._remote_reconcile_recovery import recover_target_resolution

        return recover_target_resolution(
            root, record, config, conflict_resolver, rebase_stop_resolver
        )
    if (
        record.integrated_feature_sha is None
        and _recorded_work_is_verifiably_landed(root, record)
        and _target_tip_contains_recorded_work(root, record)
    ):
        return _release_verifiably_landed_record(root, record, config)
    return None


def _target_tip_contains_recorded_work(root: Path, record: IntegrationRecord) -> bool:
    target_tip = _get_branch_sha(root, record.target)
    work_sha = _read_head_sha_or_none(root)
    if target_tip is None or work_sha is None:
        return False
    if target_tip == work_sha:
        return True
    return ancestry_state(root, work_sha, target_tip) is True


def _recorded_work_is_verifiably_landed(root: Path, record: IntegrationRecord) -> bool:
    kind = read_in_progress_kind(root)
    if not kind.is_known_good or kind.value is not InProgressKind.NONE:
        return False
    unmerged = observe_unmerged_paths(root)
    if not unmerged.query_ok or unmerged.paths:
        return False
    from ralph.pipeline._landed_predicate import operation_landed

    return operation_landed(root, record).landed_on_target


def _read_head_sha_or_none(root: Path) -> str | None:
    try:
        return get_head_sha(root)
    except (OSError, RuntimeError, ValueError, KeyError):
        return None


def _release_verifiably_landed_record(
    root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None,
) -> RebaseState:
    refresh, pointer_is_fresh = _refresh_before_verdict(config, root, record.target)
    if not pointer_is_fresh:
        return record_refresh(
            _record_skip(
                reason=(
                    "recovery: landed work observed but the target pointer"
                    " could not be refreshed, record retained for retry"
                ),
                target=record.target,
                retention_reason=StartupRetentionReason.OWNED_TARGET_PRECONDITION_FAILED,
                record_retained=True,
            ),
            refresh,
        )
    if record.reintegrate_pending:
        write_record(
            root,
            record.with_transition(MARK_REINTEGRATE_PENDING, reintegrate_pending=False),
        )
    _clear_record(root)
    from ralph.pipeline.auto_integrate_remote_sync import reconcile_remote_publication

    return record_refresh(
        reconcile_remote_publication(
            config,
            root,
            record.target,
            source_state=RebaseState(
                last_outcome=RebaseOutcomeKind.RECOVERED,
                last_reason="recorded work already contained in target",
                last_target=record.target,
                fast_forwarded=True,
            ),
        ),
        refresh,
    )


def _recover_pending_merge(
    root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    *,
    recovery_controller: RecoveryController | None = None,
    phase: str = "rebase_conflict_resolution",
    chain_agents: list[str] | tuple[str, ...] | None = None,
    config_path: Path | None = None,
    force_retry: bool = False,
    stop_identity: str | None = None,
) -> RebaseState:
    preflight = _recover_pending_merge_preflight(
        root, record, config, conflict_resolver, rebase_stop_resolver
    )
    if preflight is not None:
        return preflight
    kind = classify_git_operation(record)
    if kind in (GitOperationKind.RESOLVING_MERGE, GitOperationKind.RESOLVING_REBASE):
        if _retained_resolution_is_stale(root, record):
            return _replay_stale_resolution(
                root,
                record,
                config,
                conflict_resolver,
                rebase_stop_resolver,
            )
        resumed = continue_retained_resolution(
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
    elif kind is GitOperationKind.AWAITING_CONTINUE:
        resumed = resume_pending_rebase(root, record)
    elif kind is GitOperationKind.AWAITING_MERGE_COMMIT:
        resumed = resume_pending_merge(root, record)
    else:
        if record.is_integrating:
            return _recover_unresolved_integrating(
                root,
                record,
                config,
                conflict_resolver,
                rebase_stop_resolver,
                force_retry=force_retry,
            )
        resumed = continue_retained_resolution(
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
    if isinstance(resumed, (str, RetryLaterOutcome)):
        latest_record = read_record_outcome(root).record or record
        terminal_reason = (
            resumed.terminal_reason
            if isinstance(resumed, RetryLaterOutcome)
            and resumed.terminal_reason == ResolutionTerminationReason.CANDIDATE_EXHAUSTED
            else None
        )
        assert isinstance(terminal_reason, ResolutionTerminationReason) or terminal_reason is None
        return _resumed_failure_outcome(
            root,
            latest_record,
            resumed if isinstance(resumed, str) else resumed.on_change or "",
            terminal_reason=terminal_reason,
        )
    if isinstance(resumed, IntegrationRecord) and resumed.reintegrate_pending:
        return _replay_stale_resolution(
            root,
            resumed,
            config,
            conflict_resolver,
            rebase_stop_resolver,
        )
    return _continue_fast_forward_from_record(
        root,
        resumed,
        config,
        conflict_resolver,
        rebase_stop_resolver,
    )


recover_pending_merge = _recover_pending_merge


def _emit_critical(message: str) -> None:
    critical("CRITICAL: {}", message)


def _resumed_failure_outcome(
    root: Path,
    record: IntegrationRecord,
    reason: str,
    *,
    terminal_reason: ResolutionTerminationReason | None = None,
) -> RebaseState:
    from ralph.git.resolution_failure_cause import ResolutionFailureCause

    effective_cause = record.last_failure_cause

    if terminal_reason is ResolutionTerminationReason.CANDIDATE_EXHAUSTED:
        from ralph.pipeline.auto_integrate_outcome import record_resolution_exhausted

        integration_outcome_log().record(_emit_critical, reason, key=f"{root}\0{reason}")
        return record_resolution_exhausted(
            reason=reason,
            target=record.target,
            failure_cause=effective_cause,
            terminal_reason=terminal_reason,
        )

    if (
        not reason.startswith("WAITING:")
        and effective_cause != ResolutionFailureCause.CONFIG_FAILURE
    ):
        integration_outcome_log().record(_emit_critical, reason, key=f"{root}\0{reason}")
    if is_in_active_resolution_state(record):
        from ralph.pipeline.auto_integrate_outcome import record_conflict

        conflicted = record_conflict(
            reason=reason,
            target=record.target,
            failure_cause=effective_cause,
        )
        return conflicted.set_recovery_record_retained(
            StartupRetentionReason.OWNED_OPERATION_IDENTITY_MISMATCH
        )
    return _record_skip(
        reason=reason,
        target=record.target,
        record_retained=True,
        retention_reason=StartupRetentionReason.OWNED_OPERATION_IDENTITY_MISMATCH,
    )
