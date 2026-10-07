"""Continue a moved target under the recovery caller's existing lease."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.merge import MERGE_STATE_IN_PROGRESS, branch_sha, merge_state
from ralph.git.operations import get_head_sha, is_repo_clean
from ralph.git.rebase.rebase_continuation import rebase_in_progress_at
from ralph.pipeline.auto_integrate_record import write_record
from ralph.pipeline.integration_resolution import retained_integration_reason
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.auto_integrate_resolve import ConflictResolver
    from ralph.pipeline.conflict_resolution import RebaseStopResolver


def reintegrate_owned_landing(
    root: Path, record: IntegrationRecord, config: UnifiedConfig | None,
    conflict_resolver: ConflictResolver | None,
    rebase_stop_resolver: RebaseStopResolver | None,
) -> RebaseState:
    """Persist the next action before retrying integration without an ownership gap."""
    retained = record.model_copy(update={"reintegrate_pending": True})
    write_record(root, retained)
    resumed = _resume_owned_operation(root, retained, config, conflict_resolver, rebase_stop_resolver)
    if resumed is not None:
        return resumed
    completed = _completed_owned_action(root, retained)
    if completed is not None:
        write_record(root, completed)
        from ralph.pipeline.auto_integrate_recovery import _continue_fast_forward_from_record

        return _continue_fast_forward_from_record(
            root, completed, config, conflict_resolver, rebase_stop_resolver,
        )
    expected = (
        retained.integrated_feature_sha if retained.phase == "integrated"
        else retained.pre_feature_sha
    )
    if config is None or get_head_sha(root) != expected or not is_repo_clean(root):
        return RebaseState(
            last_action="skipped", last_target=record.target,
            last_reason="owned target reintegration requires config, unchanged feature tip, and a clean worktree",
            recovery_record_retained=True,
        )
    from ralph.pipeline._integration_continuation import config_for_owned_integration
    from ralph.pipeline.auto_integrate import _integrate_once_owned

    outcome, _retry = _integrate_once_owned(
        config_for_owned_integration(config), root, record.target, conflict_resolver,
        rebase_stop_resolver=rebase_stop_resolver, owned_record=retained,
    )
    if outcome is not None:
        return outcome.model_copy(update={
            "recovery_record_retained": retained_integration_reason(root) is not None,
        })
    return RebaseState(
        last_action="skipped", last_target=record.target,
        last_reason="owned reintegration did not produce completion proof",
        recovery_record_retained=True,
    )


def _resume_owned_operation(
    root: Path, record: IntegrationRecord, config: UnifiedConfig | None,
    conflict_resolver: ConflictResolver | None,
    rebase_stop_resolver: RebaseStopResolver | None,
) -> RebaseState | None:
    from ralph.pipeline._integration_continuation import active_operation_matches

    updates: dict[str, object]
    if rebase_in_progress_at(root):
        updates = {"resolving_rebase": True}
    elif merge_state(root) == MERGE_STATE_IN_PROGRESS:
        updates = {"resolving_merge": True}
    else:
        return None
    if not active_operation_matches(root, record):
        return RebaseState(
            last_action="skipped", last_target=record.target,
            last_reason="active operation identity differs from owned reintegration; progress retained",
            recovery_record_retained=True,
        )
    resumed = record.model_copy(update=updates)
    write_record(root, resumed)
    from ralph.pipeline.auto_integrate_recovery import _recover_pending_merge

    return _recover_pending_merge(root, resumed, config, conflict_resolver, rebase_stop_resolver)


def _completed_owned_action(root: Path, record: IntegrationRecord) -> IntegrationRecord | None:
    if record.phase != "integrating":
        return None
    if (
        get_head_sha(root) == record.pre_feature_sha
        and branch_sha(root, record.target) == record.pre_feature_sha
    ):
        return record.model_copy(update={
            "phase": "integrated", "integrated_feature_sha": record.pre_feature_sha,
        })
    if not is_repo_clean(root):
        return None
    from ralph.pipeline._integration_continuation import _legacy_completed_rebase
    from ralph.pipeline._pending_merge_commit import _git_value

    completed = _legacy_completed_rebase(root, record.model_copy(update={"resolving_rebase": True}))
    if completed is not None:
        return completed
    head = _git_value(root, "rev-parse", "--verify", "HEAD")
    parents = _git_value(root, "show", "-s", "--format=%P", "HEAD")
    previous = _git_value(root, "rev-parse", "--verify", "HEAD@{1}")
    if (
        head is None or record.pre_target_sha is None
        or parents != f"{record.pre_feature_sha} {record.pre_target_sha}"
        or previous != record.pre_feature_sha
    ):
        return None
    return record.model_copy(update={"phase": "integrated", "integrated_feature_sha": head})
