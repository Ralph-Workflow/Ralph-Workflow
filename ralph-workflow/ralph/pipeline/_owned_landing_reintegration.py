"""Continue a moved target under the recovery caller's existing lease."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.merge import (
    MERGE_STATE_IN_PROGRESS,
    branch_sha,
    merge_state,
    observe_unmerged_paths,
)
from ralph.git.merge_obstructions import ancestry_state
from ralph.git.operations import get_head_sha, is_repo_clean
from ralph.git.rebase.rebase_continuation import rebase_in_progress_at
from ralph.git.subprocess_runner import GitRunOptions, run_git
from ralph.pipeline._commit_completion_authority import CommitCompletion, complete_commit
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline.auto_integrate_backup_refs import create_stale_resolution_snapshot
from ralph.pipeline.auto_integrate_context import current_branch_or_detached_marker
from ralph.pipeline.auto_integrate_record import (
    MARK_REINTEGRATE_PENDING,
    MARK_RESOLVING_MERGE,
    MARK_RESOLVING_REBASE,
    START_INTEGRATING,
    Transition,
    write_record,
)
from ralph.pipeline.integration_observation import InProgressKind, read_in_progress_kind
from ralph.pipeline.integration_resolution import retained_integration_reason
from ralph.pipeline.rebase_state import RebaseState, StartupRetentionReason

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver


#: Porcelain v1 entries are ``XY<space>path``; the status code plus the
#: separating space is the prefix before the path starts.
_PORCELAIN_STATUS_PREFIX_LEN = 3


def retained_resolution_is_stale(root: Path, record: IntegrationRecord) -> bool:
    """Return whether Git disproves the record's retained-resolution intent.

    A stale intent has all three positive facts: no rebase, merge, or
    cherry-pick operation is in progress; the index has no unmerged
    entries; and the current feature work is not already contained in the
    target.  The record's flags are intent only -- they never override
    these observations.
    """
    return _git_shows_no_operation(root) and _work_is_not_contained(root, record.target)


def replay_stale_resolution(
    root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None,
    conflict_resolver: ConflictResolver | None,
    rebase_stop_resolver: RebaseStopResolver | None,
) -> RebaseState:
    """Preserve stale work, clear stale intent, and retry auto rebase.

    The backup snapshot is completed before the durable record changes or
    HEAD moves.  The snapshot's feature identity then becomes the
    feature branch tip, which makes the worktree clean and gives the
    ordinary auto-rebase entry a safe commit to replay.
    """
    if not retained_resolution_is_stale(root, record):
        return _retained_replay_outcome(
            record,
            "retained resolution is still owned by live Git state; replay was not started",
        )

    try:
        head_sha = get_head_sha(root)
    except Exception as exc:
        return _retained_replay_outcome(
            record,
            f"retained resolution HEAD is unreadable: {exc}",
        )
    feature_name = current_branch_or_detached_marker(root)
    try:
        backup_ref, feature_sha = create_stale_resolution_snapshot(
            root,
            feature_name,
            head_sha,
            record.owning_worktree or str(root),
        )
    except Exception as exc:
        logger.error("auto_integrate: stale-resolution snapshot failed: {}", exc)
        return _retained_replay_outcome(
            record,
            f"retained resolution work could not be snapshotted: {exc}",
        )

    if not _move_feature_to_snapshot(root, head_sha, feature_sha):
        return _retained_replay_outcome(
            record,
            f"retained resolution work is preserved in {backup_ref}, but its feature identity could not be restored",
        )

    try:
        cleared = record
        if cleared.resolving_rebase or cleared.resolving_merge:
            cleared = cleared.with_transition(
                MARK_RESOLVING_REBASE,
                resolving_rebase=False,
                resolving_merge=False,
            )
        replay_record = cleared.with_transition(
            START_INTEGRATING,
            phase="integrating",
            pre_feature_sha=feature_sha,
            pre_target_sha=branch_sha(root, record.target),
            integrated_feature_sha=None,
        )
        if replay_record.resolving_rebase or replay_record.resolving_merge:
            replay_record = replay_record.with_transition(
                MARK_RESOLVING_REBASE,
                resolving_rebase=False,
                resolving_merge=False,
            )
        replay_record = replay_record.with_transition(
            MARK_REINTEGRATE_PENDING,
            reintegrate_pending=True,
            owning_worktree=record.owning_worktree or str(root),
        )
        write_record(root, replay_record)
    except Exception as exc:
        logger.error("auto_integrate: stale-resolution record transition failed: {}", exc)
        return _retained_replay_outcome(
            record,
            f"retained resolution work is preserved in {backup_ref}, but stale intent could not be cleared: {exc}",
        )

    logger.info(
        "auto_integrate: stale resolution intent replayed from {} using feature {}",
        backup_ref,
        feature_sha[:8],
    )
    return reintegrate_owned_landing(
        root,
        replay_record,
        _replay_config(config, record.target),
        conflict_resolver,
        rebase_stop_resolver,
    )


def _git_shows_no_operation(root: Path) -> bool:
    kind = read_in_progress_kind(root)
    if not kind.is_known_good or kind.value is not InProgressKind.NONE:
        return False
    unmerged = observe_unmerged_paths(root)
    return unmerged.query_ok and not unmerged.paths


def _work_is_not_contained(root: Path, target: str) -> bool:
    if _has_user_work(root):
        return True
    target_sha = branch_sha(root, target)
    head_sha = get_head_sha(root)
    if target_sha is None or head_sha is None:
        return False
    return ancestry_state(root, head_sha, target_sha) is False


def _has_user_work(root: Path) -> bool:
    status = run_git(
        ("status", "--porcelain", "-z", "--untracked-files=normal"),
        cwd=root,
        label="auto-integrate:stale-resolution-status",
    )
    if status.returncode != 0:
        return False
    for entry in status.stdout.split("\0"):
        if len(entry) <= _PORCELAIN_STATUS_PREFIX_LEN:
            continue
        path = entry[_PORCELAIN_STATUS_PREFIX_LEN:]
        if not path.startswith(".agent/"):
            return True
    return False


def _move_feature_to_snapshot(root: Path, current_head: str, feature_sha: str) -> bool:
    if current_head != feature_sha:
        moved = run_git(
            (
                "update-ref",
                "-m",
                "ralph stale-resolution replay",
                "HEAD",
                feature_sha,
                current_head,
            ),
            cwd=root,
            label="auto-integrate:stale-resolution-feature-ref",
            options=GitRunOptions(env={"GIT_REFLOG_ACTION": "ralph stale-resolution replay"}),
        )
        if moved.returncode != 0:
            return False
    reset = run_git(
        ("reset", "--hard", feature_sha),
        cwd=root,
        label="auto-integrate:stale-resolution-reset",
    )
    return reset.returncode == 0 and get_head_sha(root) == feature_sha


def _replay_config(config: UnifiedConfig | None, target: str) -> UnifiedConfig:
    if config is not None:
        return config
    from ralph.config.models import UnifiedConfig

    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": target,
                "auto_integrate_remote_enabled": False,
            }
        }
    )


def _retained_replay_outcome(record: IntegrationRecord, reason: str) -> RebaseState:
    return RebaseState(
        last_outcome=RebaseOutcomeKind.SKIPPED,
        last_target=record.target,
        last_reason=reason,
        recovery_record_retained=True,
        recovery_record_retained_reason=StartupRetentionReason.OBSERVED_RETAINED_ON_DISK,
    )


def reintegrate_owned_landing(
    root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None,
    conflict_resolver: ConflictResolver | None,
    rebase_stop_resolver: RebaseStopResolver | None,
) -> RebaseState:
    """Persist the next action before retrying integration without an ownership gap."""
    owning_worktree = record.owning_worktree or str(root)
    retained = record.with_transition(
        MARK_REINTEGRATE_PENDING,
        reintegrate_pending=True,
        owning_worktree=owning_worktree,
    )
    write_record(root, retained)
    resumed = _resume_owned_operation(
        root, retained, config, conflict_resolver, rebase_stop_resolver
    )
    if resumed is not None:
        return resumed
    completed = _completed_owned_action(root, retained)
    if completed is not None:
        write_record(root, completed.record)
        from ralph.pipeline.auto_integrate_recovery_record import (
            _continue_fast_forward_from_record,
        )

        return _continue_fast_forward_from_record(
            root,
            completed.record,
            config,
            conflict_resolver,
            rebase_stop_resolver,
        )
    expected = (
        retained.integrated_feature_sha if retained.is_integrated else retained.pre_feature_sha
    )
    if config is None or get_head_sha(root) != expected or not is_repo_clean(root):
        return RebaseState(
            last_outcome=RebaseOutcomeKind.SKIPPED,
            last_target=record.target,
            last_reason="owned target reintegration requires config, unchanged feature tip, and a clean worktree",
            recovery_record_retained=True,
            recovery_record_retained_reason=StartupRetentionReason.OWNED_TARGET_PRECONDITION_FAILED,
        )
    from ralph.pipeline._integration_continuation import config_for_owned_integration
    from ralph.pipeline.auto_integrate import _integrate_once_owned

    outcome, _retry = _integrate_once_owned(
        config_for_owned_integration(config),
        root,
        record.target,
        conflict_resolver,
        rebase_stop_resolver=rebase_stop_resolver,
        owned_record=retained,
    )
    if outcome is not None:
        if retained_integration_reason(root) is None:
            return outcome.clear_recovery_record_retained()
        return outcome.set_recovery_record_retained(
            StartupRetentionReason.OBSERVED_RETAINED_ON_DISK
        )
    return RebaseState(
        last_outcome=RebaseOutcomeKind.SKIPPED,
        last_target=record.target,
        last_reason="owned reintegration did not produce completion proof",
        recovery_record_retained=True,
        recovery_record_retained_reason=StartupRetentionReason.OWNED_TARGET_PRECONDITION_FAILED,
    )


def _resume_owned_operation(
    root: Path,
    record: IntegrationRecord,
    config: UnifiedConfig | None,
    conflict_resolver: ConflictResolver | None,
    rebase_stop_resolver: RebaseStopResolver | None,
) -> RebaseState | None:
    from ralph.pipeline._integration_continuation import active_operation_matches

    transition: Transition
    kwargs: dict[str, object]
    if rebase_in_progress_at(root):
        transition = MARK_RESOLVING_REBASE
        kwargs = {"resolving_rebase": True}
    elif merge_state(root) == MERGE_STATE_IN_PROGRESS:
        transition = MARK_RESOLVING_MERGE
        kwargs = {"resolving_merge": True}
    else:
        return None
    if not active_operation_matches(root, record):
        return RebaseState(
            last_outcome=RebaseOutcomeKind.SKIPPED,
            last_target=record.target,
            last_reason="active operation identity differs from owned reintegration; progress retained",
            recovery_record_retained=True,
            recovery_record_retained_reason=StartupRetentionReason.OWNED_OPERATION_IDENTITY_MISMATCH,
        )
    resumed = record.with_transition(transition, **kwargs)
    write_record(root, resumed)
    from ralph.pipeline.auto_integrate_recovery_record import _recover_pending_merge

    return _recover_pending_merge(root, resumed, config, conflict_resolver, rebase_stop_resolver)


def _completed_owned_action(root: Path, record: IntegrationRecord) -> CommitCompletion | None:
    if not record.is_integrating:
        return None
    if (
        get_head_sha(root) == record.pre_feature_sha
        and branch_sha(root, record.target) == record.pre_feature_sha
    ):
        return complete_commit(
            record,
            integrated_feature_sha=record.pre_feature_sha,
            reintegrate_pending=False,
        )
    if not is_repo_clean(root):
        return None
    from ralph.pipeline._integration_continuation import _legacy_completed_rebase
    from ralph.pipeline._pending_merge_commit import _git_value

    completed = _legacy_completed_rebase(
        root,
        record.with_transition(
            MARK_RESOLVING_REBASE,
            resolving_rebase=True,
        ),
    )
    if completed is not None:
        return completed
    head = _git_value(root, "rev-parse", "--verify", "HEAD")
    parents = _git_value(root, "show", "-s", "--format=%P", "HEAD")
    previous = _git_value(root, "rev-parse", "--verify", "HEAD@{1}")
    if (
        head is None
        or record.pre_target_sha is None
        or parents != f"{record.pre_feature_sha} {record.pre_target_sha}"
        or previous != record.pre_feature_sha
    ):
        return None
    return complete_commit(
        record,
        integrated_feature_sha=head,
        reintegrate_pending=False,
    )
