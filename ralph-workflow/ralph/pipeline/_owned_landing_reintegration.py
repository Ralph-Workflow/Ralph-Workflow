"""Continue a moved target under the recovery caller's existing lease."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.merge import MERGE_STATE_IN_PROGRESS, merge_state
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
    from ralph.pipeline._pending_merge_commit import _git_value
    from ralph.pipeline.conflict_resolution.rebase_loop import current_rebase_identity

    updates: dict[str, object]
    if rebase_in_progress_at(root):
        matches = current_rebase_identity(root) == (record.pre_feature_sha, record.pre_target_sha)
        updates = {"resolving_rebase": True}
    elif merge_state(root) == MERGE_STATE_IN_PROGRESS:
        matches = (
            _git_value(root, "rev-parse", "--verify", "HEAD") == record.pre_feature_sha
            and _git_value(root, "rev-parse", "--verify", "MERGE_HEAD") == record.pre_target_sha
        )
        updates = {"resolving_merge": True}
    else:
        return None
    if not matches:
        return RebaseState(
            last_action="skipped", last_target=record.target,
            last_reason="active operation identity differs from owned reintegration; progress retained",
            recovery_record_retained=True,
        )
    resumed = record.model_copy(update=updates)
    write_record(root, resumed)
    from ralph.pipeline.auto_integrate_recovery import _recover_pending_merge

    return _recover_pending_merge(root, resumed, config, conflict_resolver, rebase_stop_resolver)
