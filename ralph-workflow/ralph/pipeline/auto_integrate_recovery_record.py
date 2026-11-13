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
from ralph.pipeline._git_operation_authority import (
    GitOperationKind,
    classify_git_operation,
    is_in_active_resolution_state,
)
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline._integration_continuation import (
    compute_continuation_retry_gate_key,
    continue_retained_resolution,
    reset_continuation_gate_state,
)
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
    RELEASE_DIAGNOSTIC_EVIDENCE,
    IntegrationRecord,
    RecordBusyError,
    RecordReadOutcomeDisposition,
    RecordUnreadableError,
    _read_record_raw,
    read_record_outcome,
    record_path,
)
from ralph.pipeline.auto_integrate_sync import REFRESH_UNREACHABLE
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
    except Exception as exc:
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
        return _record_skip(
            reason=(
                "recovery: malformed integrated record; verified feature SHA missing"
                if feature_sha is None
                else "recovery: feature tip changed or unreadable; landing withheld"
            ),
            target=record.target,
            retention_reason=StartupRetentionReason.OWNED_TARGET_PRECONDITION_FAILED,
            record_retained=True,
        )
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
    except Exception as exc:
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
) -> RebaseState:
    import ralph.pipeline.auto_integrate_recovery as _rec_mod

    if _rec_mod.merge_state(root) not in {MERGE_STATE_NONE, MERGE_STATE_IN_PROGRESS}:
        return _record_skip(
            reason="recovery: merge state unreadable; record retained for retry",
            target=record.target,
            retention_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
            record_retained=True,
        )
    if _retained_resolution_is_stale(root, record) and _recorded_identity_changed_or_dirty(
        root, record
    ):
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
    )


def _recorded_identity_changed_or_dirty(root: Path, record: IntegrationRecord) -> bool:
    expected = record.pre_feature_sha
    try:
        import ralph.git.operations as _git_ops

        head = _git_ops.get_head_sha(root)
    except Exception:
        return True
    if head != expected:
        return True
    return _has_tracked_uncommitted_work(root)


def _has_tracked_uncommitted_work(root: Path) -> bool:
    from ralph.git.subprocess_runner import run_git

    status = run_git(
        ("status", "--porcelain", "--untracked-files=no"),
        cwd=root,
        label="recovery:recorded-identity-status",
    )
    if status.returncode != 0:
        return False
    return bool(status.stdout.splitlines())


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
    if record.integrated_feature_sha is None and _recorded_work_is_verifiably_landed(root, record):
        return _release_verifiably_landed_record(root, record, config)
    return None


def _recorded_work_is_verifiably_landed(root: Path, record: IntegrationRecord) -> bool:
    kind = read_in_progress_kind(root)
    if not kind.is_known_good or kind.value is not InProgressKind.NONE:
        return False
    unmerged = observe_unmerged_paths(root)
    if not unmerged.query_ok or unmerged.paths:
        return False
    target_sha = _get_branch_sha(root, record.target)
    if target_sha is None:
        return False
    work_sha = record.integrated_feature_sha or _read_head_sha_or_none(root)
    if work_sha is None:
        return False
    ancestry = ancestry_state(root, work_sha, target_sha)
    return ancestry is True


def _read_head_sha_or_none(root: Path) -> str | None:
    try:
        return get_head_sha(root)
    except Exception:
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
    if isinstance(resumed, str):
        latest_record = read_record_outcome(root).record or record
        return _resumed_failure_outcome(
            latest_record,
            resumed,
        )
    if resumed.reintegrate_pending:
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


def _resumed_failure_outcome(
    record: IntegrationRecord,
    reason: str,
) -> RebaseState:
    from ralph.git.resolution_failure_cause import ResolutionFailureCause

    effective_cause = record.last_failure_cause

    if (
        not reason.startswith("WAITING:")
        and effective_cause != ResolutionFailureCause.CONFIG_FAILURE
    ):
        critical("CRITICAL: {}", reason)
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
