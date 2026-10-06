"""Continue retained resolver work under recovery's existing integration lease."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.merge import MERGE_STATE_IN_PROGRESS, MERGE_STATE_NONE, is_ancestor, merge_state
from ralph.git.rebase.rebase_continuation import rebase_in_progress_at
from ralph.git.subprocess_runner import run_git
from ralph.pipeline._pending_merge_commit import _git_value
from ralph.pipeline.auto_integrate_record import IntegrationRecord, read_record, write_record

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.rebase_state import RebaseState
    from ralph.workspace.scope import WorkspaceScope

_REBASE_RECEIPT_ENTRIES = 2


def continue_retained_resolution(
    root: Path, record: IntegrationRecord, config: UnifiedConfig | None,
    conflict_resolver: ConflictResolver | None,
    rebase_stop_resolver: RebaseStopResolver | None,
) -> IntegrationRecord | str:
    """Resume the recorded operation and promote only a terminal Git result."""
    target = record.pre_target_sha if record.operation_kind == "target_reconcile" else record.target
    if not target:
        return "retained resolution target unreadable; progress preserved"
    if record.resolving_merge and merge_state(root) == MERGE_STATE_IN_PROGRESS:
        if conflict_resolver is None:
            return "merge resolution retained; continuation resolver required"
        from ralph.pipeline.auto_integrate_resolve import _resolve_and_commit_with_reason

        result = _resolve_and_commit_with_reason(root, target, conflict_resolver)
        if result.outcome != "success":
            return result.reason or "merge resolution remains pending after continuation"
    elif record.resolving_rebase and rebase_in_progress_at(root):
        if rebase_stop_resolver is None:
            return "rebase resolution retained; continuation resolver required"
        from ralph.pipeline.auto_integrate_rebase_merge import _resolve_rebase_with_config

        resolved, reason = _resolve_rebase_with_config(
            root, target, rebase_stop_resolver,
            config.conflict_resolution if config is not None else None,
        )
        if not resolved:
            return reason or "rebase resolution remains pending after continuation"
    return _promote_completed_resolution(root, record)


def _promote_completed_resolution(root: Path, record: IntegrationRecord) -> IntegrationRecord | str:
    from ralph.pipeline.auto_integrate_recovery_terminal import post_attempt_verify

    post_attempt_verify(root, expected_head_sha=None, owns_resolution=False)
    if rebase_in_progress_at(root) or merge_state(root) != MERGE_STATE_NONE:
        return "retained integration operation remains active; continuation required"
    current = read_record(root)
    if current is None:
        return "retained integration ownership unreadable after continuation"
    if current.phase == "integrated":
        return current
    if current.rebase_continue_pending:
        from ralph.pipeline._pending_rebase_continue import resume_pending_rebase

        return resume_pending_rebase(root, current)
    legacy = _legacy_completed_rebase(root, current)
    if legacy is not None:
        write_record(root, legacy)
        return legacy
    return "retained resolution lacks verified completion proof; work preserved and landing withheld"


def _legacy_completed_rebase(root: Path, record: IntegrationRecord) -> IntegrationRecord | None:
    if not record.resolving_rebase or not record.pre_target_sha:
        return None
    branch = _git_value(root, "symbolic-ref", "--quiet", "HEAD")
    head = _git_value(root, "rev-parse", "--verify", "HEAD")
    if branch is None or head is None:
        return None
    history = run_git(
        ("reflog", "show", "--format=%H%x00%gs", "-2", branch),
        cwd=root, label="recovery:legacy-rebase-receipt",
    )
    rows = history.stdout.splitlines()
    if history.returncode or len(rows) != _REBASE_RECEIPT_ENTRIES:
        return None
    latest, _, action = rows[0].partition("\0")
    previous, _, _action = rows[1].partition("\0")
    if (
        latest != head or previous != record.pre_feature_sha
        or action != f"rebase (finish): {branch} onto {record.pre_target_sha}"
        or not is_ancestor(root, record.pre_target_sha, head)
    ):
        return None
    return record.model_copy(update={
        "phase": "integrated", "integrated_feature_sha": head, "resolving_rebase": False,
    })


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
        scope, config=config, conflict_resolver=conflict_resolver,
        rebase_stop_resolver=rebase_stop_resolver,
    )
    if recovery_retained_record(recovered):
        if prior_attempt is not None:
            return prior_attempt.model_copy(update={"recovery_record_retained": True})
        return recovered
    if recovered is not None and recovered.fast_forwarded:
        return recovered
    if not config.general.auto_integrate_enabled and recovered is not None:
        from ralph.pipeline.auto_integrate import auto_integrate_after_commit

        return auto_integrate_after_commit(
            config_for_owned_integration(config), scope, recovered,
            conflict_resolver=conflict_resolver, rebase_stop_resolver=rebase_stop_resolver,
        )
    return None


def config_for_owned_integration(config: UnifiedConfig) -> UnifiedConfig:
    """Disabling new integrations does not abandon an already-owned landing."""
    return config if config.general.auto_integrate_enabled else config.model_copy(update={
        "general": config.general.model_copy(update={"auto_integrate_enabled": True}),
    })
