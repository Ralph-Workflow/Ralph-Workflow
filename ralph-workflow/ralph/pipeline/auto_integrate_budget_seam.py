from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from ralph.git.errors import GitOperationError
from ralph.git.merge import branch_sha, conflict_stage_entries, unmerged_paths
from ralph.git.operations import get_head_sha
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.auto_integrate_conflict_budget import (
    MAX_TRACKED_CONFLICT_ATTEMPTS,
    ConflictIdentity,
    apply_conflict_budget,
    prior_conflict_count,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.rebase_state import RebaseState

__all__ = [
    "apply_completed_attempt_budget",
    "carry_budget_through_skip",
    "charge_failed_attempt",
    "charge_non_repository_failure",
    "observe_conflict_identity",
]


def observe_conflict_identity(
    root: Path, target: str, *, scope: str = "feature"
) -> ConflictIdentity:
    try:
        feature_sha: str | None = get_head_sha(root)
    except GitOperationError as exc:
        logger.debug("auto_integrate: conflict-identity HEAD read failed: {}", exc)
        feature_sha = None
    paths = tuple(path for path in unmerged_paths(root) if path != "<unmerged-path-query-failed>")
    entries = conflict_stage_entries(root, paths)
    stage_oids = tuple(
        f"{path}:{stage}:{blob}"
        for path in sorted(entries)
        for stage, (_mode, blob) in sorted(entries[path].items())
    )
    return ConflictIdentity(
        feature_sha=feature_sha,
        target_sha=branch_sha(root, target),
        conflicted_paths=paths,
        stage_oids=stage_oids,
        scope=scope,
    )


def carry_budget_through_skip(
    skip: RebaseState,
    *,
    prior: RebaseState,
    attempts: int = MAX_TRACKED_CONFLICT_ATTEMPTS,
) -> RebaseState:
    if skip.last_target is None:
        return skip
    return apply_conflict_budget(
        skip,
        prior=prior,
        target=skip.last_target,
        resolver_suppressed=False,
        attempts=attempts,
    )


def apply_completed_attempt_budget(
    record: RebaseState,
    *,
    prior: RebaseState,
    target: str,
    identity: ConflictIdentity,
    resolver_suppressed: bool,
    attempts: int = MAX_TRACKED_CONFLICT_ATTEMPTS,
) -> RebaseState:
    if record.last_failure_cause == ResolutionFailureCause.AGENT_FAILURE:
        return charge_failed_attempt(
            record, prior=prior, target=target, identity=identity, attempts=attempts
        )
    if record.last_failure_cause in (
        ResolutionFailureCause.CONFIG_FAILURE,
        ResolutionFailureCause.CONTEXT_FAILURE,
    ):
        return charge_non_repository_failure(record, prior=prior, target=target, attempts=attempts)
    return apply_conflict_budget(
        record,
        prior=prior,
        target=target,
        resolver_suppressed=resolver_suppressed,
        identity=identity,
        attempts=attempts,
    )


def charge_non_repository_failure(
    skip: RebaseState,
    *,
    prior: RebaseState,
    target: str,
    attempts: int = MAX_TRACKED_CONFLICT_ATTEMPTS,
) -> RebaseState:
    carried = prior_conflict_count(prior, target, attempts=attempts)
    updated = (
        skip.record_consecutive_conflicts(carried, cap=attempts)
        .set_retry_deadline(
            skip.retry_deadline if skip.retry_deadline is not None else prior.retry_deadline
        )
        .project_conflict_strategy_from(prior)
    )
    return updated.model_copy(
        update={
            "last_conflict_feature_sha": prior.last_conflict_feature_sha,
            "last_conflict_target_sha": prior.last_conflict_target_sha,
            "last_conflict_paths": prior.last_conflict_paths,
            "last_conflict_stage_oids": prior.last_conflict_stage_oids,
            "last_conflict_scope": prior.last_conflict_scope,
            "last_conflict_strategy_index": prior.last_conflict_strategy_index,
        }
    )


def charge_failed_attempt(
    skip: RebaseState,
    *,
    prior: RebaseState,
    target: str,
    identity: ConflictIdentity,
    attempts: int = MAX_TRACKED_CONFLICT_ATTEMPTS,
) -> RebaseState:
    carried = prior_conflict_count(prior, target, identity, attempts=attempts)
    updated = skip.record_consecutive_conflicts(carried, cap=attempts)
    return updated.model_copy(
        update={
            "last_conflict_feature_sha": identity.feature_sha,
            "last_conflict_target_sha": identity.target_sha,
            "last_conflict_paths": identity.conflicted_paths,
            "last_conflict_stage_oids": identity.stage_oids,
            "last_conflict_strategy_index": prior.conflict_strategy_index,
        }
    )
