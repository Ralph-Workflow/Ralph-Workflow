"""Diagnostic-evidence / record-store path split from :mod:`ralph.pipeline.auto_integrate_recovery`.

Owns every helper that treats the typed :class:`IntegrationRecord` as
the source of truth: target refresh, the fast-forward / land-and-
reconcile continuations, the BUSY/UNREADABLE refusal mapping, and the
pending-merge / pending-rebase continuation that decides which of those
two paths to take. The orchestrator keeps the entry point
(:func:`recover_incomplete_integration`) and the record-read / parse
gates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

from loguru import logger

from ralph.git.merge import (
    MERGE_STATE_IN_PROGRESS,
    MERGE_STATE_NONE,
)
from ralph.git.operations import is_repo_clean
from ralph.pipeline._git_operation_authority import (
    GitOperationKind,
    classify_git_operation,
    is_in_active_resolution_state,
)
from ralph.pipeline._integration_action import (
    IntegrationAction,
    RebaseOutcomeKind,
    next_integration_action,
)
from ralph.pipeline._integration_continuation import continue_retained_resolution
from ralph.pipeline._owned_landing_reintegration import (
    reintegrate_owned_landing as _reintegrate_owned_landing,
)
from ralph.pipeline._pending_merge_commit import _git_value, resume_pending_merge
from ralph.pipeline._pending_rebase_continue import resume_pending_rebase
from ralph.pipeline._recovery_fast_forward_landing import (
    _record_skip,
)
from ralph.pipeline.auto_integrate_context import (
    record_refresh,
    refresh_outcome_is_healthy,
)
from ralph.pipeline.auto_integrate_record import (
    MARK_RESOLVING_MERGE,
    MARK_RESOLVING_REBASE,
    RELEASE_DIAGNOSTIC_EVIDENCE,
    IntegrationRecord,
    RecordBusyError,
    RecordReadOutcomeDisposition,
    RecordUnreadableError,
    read_record_outcome,
)
from ralph.pipeline.auto_integrate_sync import REFRESH_UNREACHABLE
from ralph.pipeline.rebase_state import RebaseState, StartupRetentionReason

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.mcp.artifacts.file_backend import FileBackend
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.workspace.scope import WorkspaceScope


__all__ = [
    "_continue_fast_forward_from_record",
    "_maybe_release_diagnostic",
    "_read_outcome_or_skip",
    "_recover_pending_merge",
    "_recover_unresolved_integrating",
    "_refresh_before_verdict",
]


def _refresh_before_verdict(
    config: UnifiedConfig | None,
    workspace_root: Path,
    target: str,
) -> tuple[str | None, bool]:
    """Re-read the mainline pointer the recovery verdicts are taken from.

    Landing and reintegration must use a refreshed target observation;
    failed refresh preserves the durable obligation for retry.

    Returns ``(outcome, pointer_is_fresh)``:

    * ``outcome`` is the ``REFRESH_*`` verb the caller stamps on the
      returned state, or ``None`` when no config was supplied.
    * ``pointer_is_fresh`` says whether the target pointer the verdicts
      are about to read can be trusted. It is ``True`` when no config
      was supplied -- that caller opted out of the seam entirely and
      keeps the pre-seam behaviour byte-for-byte -- and otherwise only
      when the refresh actually returned a HEALTHY outcome
      (:func:`~ralph.pipeline.auto_integrate_context.refresh_outcome_is_healthy`).
      A refresh that RAISED, or that came back unreachable / diverged /
      race-lost, establishes no fresh verdict, so the caller must fail
      closed rather than clear the durable record from a pointer
      nothing vouched for.

    A raise is reported as
    :data:`~ralph.pipeline.auto_integrate_sync.REFRESH_UNREACHABLE`
    rather than ``None`` so the skip the caller returns names why the
    landing was deferred instead of looking like no refresh was even
    attempted. Never raises: recovery's contract is that it swallows
    everything.
    """
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
    """Best-effort continue of an unfinished fast-forward (phase='integrated').

    # AC-14 rationale: F6
    # AC-14 rationale: G3
    # AC-14 rationale: G4
    # AC-14 rationale: H7
    Retain transient failures and missing completion identities for retry.
    Clear ownership only after a verified landing. Target movement schedules
    owned reintegration. When refresh cannot establish a current target pointer,
    fail closed and retain the record rather than deciding from stale state.
    """
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
        # Fail closed. Neither ancestry verdict below may be taken from
        # a pointer nothing vouched for, because both of them clear the
        # durable record and one of them does so permanently. Retain it
        # and let the next startup retry against a pointer that can be
        # refreshed.
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
        # Already landed (another run / operator). Route through the
        # reconciler so an interrupted prior run can still publish the
        # target to its configured remote without changing the local
        # landing. The reconciler compares local and remote SHAs and
        # only pushes when the remote is missing the landed commit.
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
    """Read the durable record outcome; map UNREADABLE to a typed intervention state.

    Returns ``None`` when the outcome is PRESENT or ABSENT (the
    caller proceeds with the recovery). Returns a typed
    :class:`RebaseState` for UNREADABLE bytes (intervention, bytes
    preserved) or an unreadable read exception (retained skip).
    """
    try:
        outcome = read_record_outcome(root, backend=backend)
    except Exception as exc:
        logger.critical("Integration ownership unreadable; recovery retained: {}", exc)
        return _record_skip(
            reason=f"integration ownership unreadable: {exc}",
            target=None,
            retention_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
            record_retained=True,
        )
    if outcome.disposition is RecordReadOutcomeDisposition.UNREADABLE:
        # Decision-complete startup handoff (U4d): a PRESENT-but-unreadable
        # record cannot answer the recovery authorities, so the startup
        # path returns the same typed intervention decision as the other
        # entry points instead of a generic retained skip. The unreadable
        # bytes stay untouched on disk as ownership evidence.
        reason = "durable integration record unreadable; intervention required" + (
            f" ({outcome.unreadable_reason})" if outcome.unreadable_reason else ""
        )
        return RebaseState(
            last_outcome=RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED,
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
    """Apply the release transition through the typed record store, or return a refusal.

    The same transition path is used for the UNCHANGED (held lease
    valid) and UNKNOWN (expired or unparseable lease, fail-open
    auto-release) verdict branches. The verdict-specific comments
    stay on the orchestrator callsite.
    """
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
        # Fail closed: bytes untouched. The bounded wait vs
        # intervention decision belongs to the run loop's typed
        # authority over the PERSISTED ``RebaseState.retry_deadline``
        # (checkpoint), not to a fresh allowance minted here. The
        # retained-skip outcome carries no deadline, so a caller
        # without a persisted unexpired deadline routes to
        # REQUIRE_INTERVENTION (see busy_refusal_outcome).
        return _record_skip(
            reason="integration record busy; recovery will retry",
            target=record.target,
            retention_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
            record_retained=True,
        )
    except RecordUnreadableError:
        return RebaseState(
            last_outcome=RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED,
            last_reason=("durable integration record unreadable at release; intervention required"),
            last_target=record.target,
            recovery_record_retained=True,
            recovery_record_retained_reason=(StartupRetentionReason.OBSERVED_RETAINED_ON_DISK),
        )
    # Re-read after release; the on-disk state is the source of truth.
    return read_record_outcome(root, backend=backend).record


def _maybe_release_diagnostic(
    root: Path,
    workspace_scope: WorkspaceScope,
    backend: FileBackend,
) -> IntegrationRecord | RebaseState | None:
    """Apply the release path through the typed record store, or return a refusal.

    Returns ``None`` when there is no diagnostic evidence to release
    (caller proceeds with the recovery). Returns the
    :class:`IntegrationRecord` (unchanged) on a successful release.
    Returns a typed :class:`RebaseState` for BUSY/UNREADABLE
    refusals (bounded retry vs intervention) or CHANGED
    diagnostic verdict (retained skip).
    """
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
            # UNCHANGED: held lease valid; release.
            # UNKNOWN: expired or unparseable lease: fail-open to auto-release.
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
    return _reintegrate_owned_landing(
        root,
        record,
        config,
        conflict_resolver,
        rebase_stop_resolver,
    )


def _recover_pending_merge(
    root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
) -> RebaseState:
    if record.operation_kind == "target_reconcile":
        from ralph.pipeline._remote_reconcile_recovery import recover_target_resolution

        return recover_target_resolution(
            root, record, config, conflict_resolver, rebase_stop_resolver
        )
    if next_integration_action(record) == IntegrationAction.REINTEGRATE_AGAINST_TARGET:
        return _recover_unresolved_integrating(
            root,
            record,
            config,
            conflict_resolver,
            rebase_stop_resolver,
        )
    import ralph.pipeline.auto_integrate_recovery as _rec_mod

    if (
        (record.resolving_merge or record.resolving_rebase)
        and not _rec_mod.rebase_in_progress(root)
        and _rec_mod.merge_state(root) == MERGE_STATE_NONE
        and is_repo_clean(root)
        and _git_value(root, "rev-parse", "--verify", "HEAD") == record.pre_feature_sha
    ):
        cleared_record = record.with_transition(
            MARK_RESOLVING_REBASE, resolving_rebase=False
        ).with_transition(MARK_RESOLVING_MERGE, resolving_merge=False)
        return _reintegrate_owned_landing(
            root,
            cleared_record,
            config,
            conflict_resolver,
            rebase_stop_resolver,
        )
    kind = classify_git_operation(record)
    if kind in (GitOperationKind.RESOLVING_MERGE, GitOperationKind.RESOLVING_REBASE):
        resumed = continue_retained_resolution(
            root,
            record,
            config,
            conflict_resolver,
            rebase_stop_resolver,
        )
    else:
        resumed = (
            resume_pending_rebase(root, record)
            if kind is GitOperationKind.AWAITING_CONTINUE
            else resume_pending_merge(root, record)
        )
    if isinstance(resumed, str):
        logger.critical("CRITICAL: {}", resumed)
        if is_in_active_resolution_state(record):
            from ralph.pipeline.auto_integrate_outcome import record_conflict
            from ralph.pipeline.rebase_state import ResolutionFailureCause

            conflicted = record_conflict(
                reason=resumed,
                target=record.target,
                failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
            )
            return conflicted.set_recovery_record_retained(
                StartupRetentionReason.OWNED_OPERATION_IDENTITY_MISMATCH
            )
        return _record_skip(
            reason=resumed,
            target=record.target,
            record_retained=True,
            retention_reason=StartupRetentionReason.OWNED_OPERATION_IDENTITY_MISMATCH,
        )
    return _continue_fast_forward_from_record(
        root,
        resumed,
        config,
        conflict_resolver,
        rebase_stop_resolver,
    )
