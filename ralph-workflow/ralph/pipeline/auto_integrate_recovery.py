"""Startup crash recovery for interrupted auto-integrations (AC-11).

This module is the entry point. The reusable pieces live next to it:

* :mod:`ralph.pipeline.auto_integrate_recovery_legacy` -- the
  ``legacy_rebase_startup_block`` entry point and the per-marker
  reclaim helpers that close the wt-23 silent-noop bug.
* :mod:`ralph.pipeline.auto_integrate_recovery_record` -- the
  diagnostic-evidence / record-store path: target refresh, the
  fast-forward / land-and-reconcile continuations, the
  BUSY/UNREADABLE refusal mapping, and the pending-merge /
  pending-rebase continuation.

Every private helper the broader pipeline imports by name from
``ralph.pipeline.auto_integrate_recovery`` is re-exported here so the
seam stays stable.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import MERGE_STATE_NONE, abort_merge, branch_sha, merge_state
from ralph.git.merge_obstructions import ancestry_state
from ralph.git.rebase.rebase import rebase_in_progress
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.pipeline._auto_integrate_config import configured_target as _configured_target
from ralph.pipeline._git_operation_authority import (
    GitOperationKind,
    classify_git_operation,
)
from ralph.pipeline._integration_action import (
    IntegrationAction,
    RebaseOutcomeKind,
    next_integration_action,
)
from ralph.pipeline._integration_diagnostic import _busy_refusal_state
from ralph.pipeline._recovery_fast_forward_landing import (
    _land_and_reconcile,
    _record_skip,
)
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    RecordReadOutcomeDisposition,
    read_record_outcome,
)
from ralph.pipeline.auto_integrate_record import (
    clear_record as _clear_record,
)
from ralph.pipeline.auto_integrate_record import (
    read_record as _read_record,
)
from ralph.pipeline.auto_integrate_record_store import apply_record_transition
from ralph.pipeline.auto_integrate_recovery_legacy import (
    _reclaim_unowned_stale_rebase,
    legacy_rebase_startup_block,
)
from ralph.pipeline.auto_integrate_recovery_record import (
    _continue_fast_forward_from_record,
    _maybe_release_diagnostic,
    _read_outcome_or_skip,
    _recover_pending_merge,
)
from ralph.pipeline.auto_integrate_recovery_terminal import (
    TerminalStateViolationError,
    _head_matches_sha,
    post_attempt_verify,
)
from ralph.pipeline.auto_integrate_refresh import refresh_target as _refresh_target
from ralph.pipeline.auto_integrate_remote_sync import (
    reconcile_remote_publication,
    remote_sync_enabled,
    remote_target_name,
)
from ralph.pipeline.auto_integrate_sync import REFRESH_UNREACHABLE
from ralph.pipeline.auto_integrate_transaction import integration_transaction
from ralph.pipeline.conflict_resolution.abort import abort_rebase_discarding_progress
from ralph.pipeline.integration_resolution import retained_integration_reason
from ralph.pipeline.rebase_state import RebaseState, StartupRetentionReason

if TYPE_CHECKING:
    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.workspace.scope import WorkspaceScope


__all__ = [
    "TerminalStateViolationError",
    "busy_refusal_outcome",
    "legacy_rebase_startup_block",
    "post_attempt_verify",
    "recover_incomplete_integration",
    "recovery_retained_record",
]


def busy_refusal_outcome(
    retry_deadline: float | None,
    *,
    clock: object = None,
) -> IntegrationAction:
    """Map a persisted ``RebaseState.retry_deadline`` to the typed retry decision.

    Delegates to the ``next_integration_action`` conflict authority: an
    unexpired persisted deadline yields ``WAIT_FOR_BOUNDED_RETRY``; a
    missing, malformed, or expired deadline yields
    ``REQUIRE_INTERVENTION``. Used by the release-refusal BUSY sites.
    """
    return next_integration_action(_busy_refusal_state(retry_deadline), clock=clock)


def recovery_retained_record(state: RebaseState | None) -> bool:
    """Whether ``state`` came back still OWNING the durable record.

    :func:`recover_incomplete_integration` has two shapes of outcome.
    Either it reconciled the interrupted operation -- cleared the
    durable :class:`~ralph.pipeline.auto_integrate_record.IntegrationRecord`
    after proving the feature branch was restored or the fast-forward
    landed -- or it hit a transient failure and deliberately LEFT the
    record on disk so the next startup can retry it.

    In the second shape recovery is not finished, and the caller must
    NOT start a fresh integration in the same startup:
    ``auto_integrate._integrate_once`` writes a new
    ``IntegrationRecord(phase='integrating', ...)`` before it touches
    git, which overwrites the only durable metadata describing the
    still-unreconciled operation -- the pre-integration feature SHA a
    later recovery needs to restore, and the target the interrupted
    landing was aimed at.

    Callers read the retention fact THROUGH this predicate rather than
    from ``last_reason``: that string is operator display text, is
    formatted differently on every branch, and interpolates exception
    messages. ``None`` (no record existed, recovery was a no-op) is not
    retained.
    """
    return state is not None and state.recovery_record_retained


def recover_incomplete_integration(
    workspace_scope: WorkspaceScope,
    *,
    config: UnifiedConfig | None = None,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> RebaseState | None:
    """Recover owned Git operations under an exclusive worktree lease.

    ``workspace_scope`` selects the repository; optional ``config`` controls
    target freshness and publication. Return the recovery verdict, or ``None``
    when no operation needs recovery. Contention retains recovery state for
    retry. Restoration refuses to overwrite unfinished edits, and a durable
    record survives until restoration or target landing is proven.
    """
    with integration_transaction(Path(workspace_scope.root)) as acquired:
        if not acquired:
            return _record_skip(
                reason="integration worktree busy; recovery will retry",
                target=None,
                retention_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
                record_retained=True,
            )
        # Startup seam: if a previous run landed locally but never
        # pushed, the reconciler catches up on this start. The
        # publication outcome is annotated on the durable record and
        # on the recovery's RebaseState return; the next stage
        # consumes it like any other landing.
        startup_publication: RebaseState | None = None
        if config is not None and remote_sync_enabled(config):
            target_obj = _configured_target(config)
            if isinstance(target_obj, str) and target_obj:
                startup_publication = reconcile_remote_publication(
                    config,
                    Path(workspace_scope.root),
                    target_obj,
                    remote=remote_target_name(config),
                    rebase_stop_resolver=rebase_stop_resolver,
                )

        record_outcome = read_record_outcome(Path(workspace_scope.root), backend=backend)
        if record_outcome.disposition is RecordReadOutcomeDisposition.UNREADABLE:
            reason = "durable integration record unreadable; intervention required" + (
                f" ({record_outcome.unreadable_reason})" if record_outcome.unreadable_reason else ""
            )
            return RebaseState(
                last_outcome=RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED,
                last_reason=reason,
                last_target=None,
                recovery_record_retained=True,
                recovery_record_retained_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
            )

        early = _read_outcome_or_skip(Path(workspace_scope.root), backend)
        if early is not None:
            return early
        record = _maybe_release_diagnostic(Path(workspace_scope.root), workspace_scope, backend)
        if isinstance(record, RebaseState):
            return record
        recovered = _recover_incomplete_integration_owned(
            workspace_scope,
            config=config,
            conflict_resolver=conflict_resolver,
            rebase_stop_resolver=rebase_stop_resolver,
            backend=backend,
        )
        if (
            recovered is None
            and startup_publication is not None
            and startup_publication.last_push_status == "pushed"
        ):
            return startup_publication
        return recovered


def _recover_incomplete_integration_owned(
    workspace_scope: WorkspaceScope,
    *,
    config: UnifiedConfig | None = None,
    conflict_resolver: ConflictResolver | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> RebaseState | None:
    """Recover from an interrupted integration at run-loop startup.

    Behavior (AC-11):

    * No durable record → no-op (never disturb an operator's own
      git operation).
    * Preserve agent-owned resolution and verified pending commit/continuation
      records; retry prepared operations before considering generic cleanup.
    * Every outcome that RETAINS the record sets
      ``RebaseState.recovery_record_retained``, which callers read
      through :func:`recovery_retained_record`. A caller that sees it
      must not start a fresh integration in the same startup: the
      integration writes its own durable record before mutating git
      and would overwrite the unreconciled one.

    Args:
        workspace_scope: Scope whose root holds the durable record.
        config: Run configuration for owned continuation and target refresh.

    Any unexpected exception inside this function is logged and
    swallowed; it must never abort the run.
    """
    try:
        root = Path(workspace_scope.root)
        record = _read_record(root, backend=backend)
        if record is None:
            retained = retained_integration_reason(root)
            return (
                _record_skip(
                    reason=retained,
                    target=None,
                    record_retained=True,
                    retention_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
                )
                if retained is not None
                else _reclaim_unowned_stale_rebase(root)
            )

        if (
            classify_git_operation(record) is not GitOperationKind.NONE
            or (
                record.operation_kind == "feature_integrate"
                and next_integration_action(record) == IntegrationAction.REINTEGRATE_AGAINST_TARGET
            )
            or (record.operation_kind == "target_reconcile")
        ):
            return _recover_pending_merge(
                root, record, config, conflict_resolver, rebase_stop_resolver
            )

        if rebase_in_progress(root) or merge_state(root) != MERGE_STATE_NONE:
            return _record_skip(
                reason="recovery: later Git operation is not owned by completed landing; finish it before retry",
                target=record.target,
                retention_reason=StartupRetentionReason.OWNED_OPERATION_IDENTITY_MISMATCH,
                record_retained=True,
            )
        return _continue_fast_forward_from_record(
            root,
            record,
            config,
            conflict_resolver,
            rebase_stop_resolver,
        )

    except Exception as exc:
        logger.warning("recover_incomplete_integration failed: {}", exc)
        return _record_skip(
            reason=f"recovery failed: {exc}",
            target=None,
            record_retained=True,
            retention_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
        )


# Re-export the private helpers imported by sibling ralph/ modules
# (notably :mod:`ralph.pipeline._owned_landing_reintegration`,
# :mod:`ralph.pipeline.auto_integrate_precondition`, and
# :mod:`ralph.pipeline.auto_integrate_catalog_rationales`) so their
# existing import paths keep resolving.
__all__ = [
    "MERGE_STATE_NONE",
    "REFRESH_UNREACHABLE",
    "IntegrationRecord",
    "TerminalStateViolationError",
    "_clear_record",
    "_head_matches_sha",
    "_land_and_reconcile",
    "_read_record",
    "_refresh_target",
    "abort_merge",
    "abort_rebase_discarding_progress",
    "ancestry_state",
    "apply_record_transition",
    "branch_sha",
    "busy_refusal_outcome",
    "legacy_rebase_startup_block",
    "merge_state",
    "post_attempt_verify",
    "read_record_outcome",
    "rebase_in_progress",
    "recover_incomplete_integration",
    "recovery_retained_record",
]


# ----- AC-14 catalog evidence -----
# AC-14 rationale: A1
# ladder rung: 2
# AC-14 rationale: A10
# ladder rung: 2
# AC-14 rationale: A11
# ladder rung: 2
# AC-14 rationale: A3
# ladder rung: 2
# AC-14 rationale: A4
# ladder rung: 1
# AC-14 rationale: A5
# ladder rung: 2
# AC-14 rationale: A6
# ladder rung: 2
# AC-14 rationale: A9
# ladder rung: 2
# AC-14 rationale: C5
# ladder rung: 2
# AC-14 rationale: E10
# ladder rung: 4
# AC-14 rationale: E7
# ladder rung: 1
# AC-14 rationale: E9
# ladder rung: 3
# AC-14 rationale: F6
# ladder rung: 3
# AC-14 rationale: G3
# ladder rung: 1
# AC-14 rationale: G4
# ladder rung: 1
# AC-14 rationale: H7
# ladder rung: 4
