from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline.auto_integrate_backup_refs import qualified_backup_ref
from ralph.pipeline.auto_integrate_context import record_refresh
from ralph.pipeline.auto_integrate_ff import fast_forward_target
from ralph.pipeline.auto_integrate_record import IntegrationRecord
from ralph.pipeline.auto_integrate_record import clear_record as _clear_record
from ralph.pipeline.auto_integrate_recovery_terminal import (
    TerminalStateViolationError,
    post_attempt_verify,
)
from ralph.pipeline.auto_integrate_recovery_terminal import (
    delete_rebase_backup_refs as _delete_rebase_backup_refs,
)
from ralph.pipeline.auto_integrate_remote_sync import reconcile_remote_publication
from ralph.pipeline.rebase_state import RebaseState, StartupRetentionReason

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig


def _land_and_reconcile(
    workspace_root: Path,
    record: IntegrationRecord,
    feature_sha: str,
    refresh: str | None,
    config: UnifiedConfig | None = None,
) -> RebaseState:
    ff_failed = False
    skip_reason = ""
    try:
        from ralph.pipeline.auto_integrate_remote_sync import reclaim_target_worktree_enabled

        ok, skip_reason = fast_forward_target(
            workspace_root,
            record.target,
            feature_sha,
            reclaim_target_worktree=reclaim_target_worktree_enabled(config),
        )
    except Exception as exc:
        ff_failed = True
        skip_reason = f"fast-forward raised: {exc}"
        logger.warning("recovery: fast_forward_target raised: {}", exc)
        ok = False
    expected_head_sha_for_verify: str | None = None
    if not ok and not ff_failed:
        expected_head_sha_for_verify = feature_sha
    try:
        post_attempt_verify(
            workspace_root,
            expected_head_sha=expected_head_sha_for_verify,
            owns_resolution=False,
        )
    except TerminalStateViolationError:
        logger.warning(
            "recovery: terminal-state invariant violation after fast-forward; "
            "the record and backup refs are RETAINED so the next recovery "
            "can still restore the pre-attempt tip. The leaked marker must "
            "be reclaimed manually or on the next attempt."
        )
        return record_refresh(
            _record_skip(
                reason=("recovery: terminal-state invariant violated; record retained for retry"),
                target=record.target,
                retention_reason=StartupRetentionReason.OWNED_TARGET_PRECONDITION_FAILED,
                record_retained=True,
            ),
            refresh,
        )
    if ok:
        _delete_rebase_backup_refs(
            workspace_root,
            qualified_backup_ref(
                record.owning_worktree or str(workspace_root), record.pre_feature_sha
            ),
        )
        _clear_record(workspace_root)
        return record_refresh(
            reconcile_remote_publication(
                config,
                workspace_root,
                record.target,
                source_state=RebaseState(
                    last_outcome=RebaseOutcomeKind.RECOVERED,
                    last_reason=None,
                    last_target=record.target,
                    fast_forwarded=True,
                ),
            ),
            refresh,
        )
    if ff_failed or "advanced concurrently" not in skip_reason:
        return record_refresh(
            _record_skip(
                reason=f"recovery: {skip_reason}; record retained for retry",
                target=record.target,
                retention_reason=StartupRetentionReason.TRANSIENT_FAST_FORWARD_FAILURE,
                record_retained=True,
            ),
            refresh,
        )
    return record_refresh(
        _record_skip(
            reason=f"recovery: {skip_reason}",
            target=record.target,
            retention_reason=StartupRetentionReason.TRANSIENT_FAST_FORWARD_FAILURE,
            record_retained=True,
        ),
        refresh,
    )


def _record_skip(
    *,
    reason: str,
    target: str | None,
    record_retained: bool = False,
    retention_reason: StartupRetentionReason | None = None,
) -> RebaseState:
    return RebaseState(
        last_outcome=RebaseOutcomeKind.SKIPPED,
        last_reason=reason,
        last_target=target,
        fast_forwarded=False,
        recovery_record_retained=record_retained,
        recovery_record_retained_reason=retention_reason if record_retained else None,
    )
