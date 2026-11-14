from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from loguru import logger

from ralph.git.merge import (
    WORKTREE_FOUND,
    WORKTREE_QUERY_FAILED,
    compare_and_swap_branch,
    fast_forward_via_worktree,
    is_ancestor,
    observe_branch_sha,
    worktree_lookup,
)
from ralph.git.operations import find_main_worktree_root
from ralph.pipeline._auto_integrate_reclaim import ReclaimResult, reclaim_dirty_target_worktree
from ralph.pipeline.resolution_outcome import (
    ConfigFailureOutcome,
    NextStrategyOutcome,
    ResolvedOutcome,
    RetryLaterOutcome,
    StopResolutionOutcome,
    UnknownOutcome,
    WaitingOutcome,
)

_TARGET_MISSING = "target branch missing at fast-forward time"
_TARGET_QUERY_FAILED = "target branch could not be read at fast-forward time"
_TARGET_NOT_ANCESTOR = "target advanced concurrently (not an ancestor of feature)"
_TARGET_CAS_MISMATCH = "target advanced concurrently (CAS mismatch)"
_TARGET_WORKTREE_QUERY_FAILED = "target worktree lookup failed"
_TARGET_CHECKED_OUT_REFUSED = (
    "target is checked out in a sibling worktree that refused merge --ff-only; "
    "shared ref left untouched, next clean seam will retry"
)
_RETRYABLE_REASONS = frozenset(
    {
        _TARGET_NOT_ANCESTOR,
        _TARGET_CAS_MISMATCH,
        _TARGET_QUERY_FAILED,
        _TARGET_WORKTREE_QUERY_FAILED,
        _TARGET_CHECKED_OUT_REFUSED,
    }
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.git.remote_push import PushResult
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.rebase_state import RebaseState


class RemoteClient(Protocol):
    def refresh_target(
        self,
        repo_root: Path,
        target: str,
        *,
        timeout_seconds: float,
        remote: str,
    ) -> str: ...

    def push_branch(
        self,
        repo_root: Path,
        branch: str,
        *,
        remote: str,
        timeout_seconds: float,
    ) -> PushResult: ...

    def observe_remote_sha(
        self,
        repo_root: Path,
        target: str,
        *,
        remote: str,
    ) -> str | None: ...


def retry_pending_remote_publish(
    config: UnifiedConfig | None,
    repo_root: Path,
    target: str,
    record: RebaseState,
    *,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    remote_client: RemoteClient | None = None,
) -> RebaseState | None:
    from ralph.pipeline.auto_integrate_remote_sync import (
        reconcile_remote_publication,
        remote_sync_enabled,
    )

    if not remote_sync_enabled(config):
        return None
    return reconcile_remote_publication(
        config,
        repo_root,
        target,
        remote=record.last_remote,
        remote_client=remote_client,
        rebase_stop_resolver=rebase_stop_resolver,
        source_state=record,
    )


def fast_forward_target(
    repo_root: Path,
    target: str,
    feature_sha: str,
    *,
    reclaim_target_worktree: bool = True,
    on_reclaimed: Callable[[ReclaimResult], None] | None = None,
) -> StopResolutionOutcome:
    early_reason = _landing_gate_reason(repo_root, target, feature_sha)
    if early_reason:
        return _fast_forward_failure(early_reason)
    primary_root = find_main_worktree_root(repo_root)
    verdict, wt = worktree_lookup(primary_root, target)
    if verdict == WORKTREE_QUERY_FAILED:
        logger.warning(
            "auto_integrate: could not determine whether '{}' is checked out; "
            "refusing to move the shared ref",
            target,
        )
        return _fast_forward_failure(_TARGET_WORKTREE_QUERY_FAILED)
    if verdict == WORKTREE_FOUND and wt is not None:
        return _fast_forward_via_target_worktree(
            repo_root,
            wt,
            target,
            feature_sha,
            reclaim_target_worktree=reclaim_target_worktree,
            on_reclaimed=on_reclaimed,
        )
    return _fast_forward_via_cas(repo_root, target, feature_sha)


def _landing_gate_reason(repo_root: Path, target: str, feature_sha: str) -> str:
    _observed_sha, reason = _observe_landable_target(repo_root, target, feature_sha)
    return reason


def _observe_landable_target(
    repo_root: Path,
    target: str,
    feature_sha: str,
) -> tuple[str | None, str]:
    observed_target_sha, query_ok = observe_branch_sha(repo_root, target)
    if not query_ok:
        return None, _TARGET_QUERY_FAILED
    if observed_target_sha is None:
        return None, _TARGET_MISSING
    if not is_ancestor(repo_root, observed_target_sha, feature_sha):
        return None, _TARGET_NOT_ANCESTOR
    return observed_target_sha, ""


def _fast_forward_via_target_worktree(
    repo_root: Path,
    worktree_root: Path,
    target: str,
    feature_sha: str,
    *,
    reclaim_target_worktree: bool,
    on_reclaimed: Callable[[ReclaimResult], None] | None,
) -> StopResolutionOutcome:
    if fast_forward_via_worktree(worktree_root, feature_sha):
        return ResolvedOutcome()
    reclaimed: ReclaimResult | None = None
    if reclaim_target_worktree:
        try:
            reclaimed = reclaim_dirty_target_worktree(worktree_root, target)
        except OSError:
            reclaimed = None
        if reclaimed is not None and fast_forward_via_worktree(worktree_root, feature_sha):
            if on_reclaimed is not None:
                on_reclaimed(reclaimed)
            return ResolvedOutcome()
    logger.warning(
        "auto_integrate: target '{}' is checked out in {} and refused "
        "merge --ff-only; leaving shared ref untouched and recording "
        "a loud retryable skip (AC-10/E2). The next clean seam will "
        "retry the same merge --ff-only once the worktree is clean.",
        target,
        worktree_root,
    )
    return _fast_forward_failure(_TARGET_CHECKED_OUT_REFUSED)


def _fast_forward_via_cas(
    repo_root: Path,
    target: str,
    feature_sha: str,
) -> StopResolutionOutcome:
    fresh_target_sha, reason = _observe_landable_target(repo_root, target, feature_sha)
    if fresh_target_sha is None:
        return _fast_forward_failure(reason)
    if not compare_and_swap_branch(repo_root, target, fresh_target_sha, feature_sha):
        return _fast_forward_failure(_TARGET_CAS_MISMATCH)
    return ResolvedOutcome()


def _fast_forward_failure(reason: str) -> StopResolutionOutcome:
    if reason in _RETRYABLE_REASONS:
        return RetryLaterOutcome(on_change=reason)
    return WaitingOutcome(external_condition=reason, resumes_when="target branch exists")


def is_retryable_fast_forward_failure(outcome: StopResolutionOutcome | str) -> bool:
    if isinstance(outcome, str):
        return outcome in _RETRYABLE_REASONS
    if isinstance(outcome, (tuple, bool)):
        return False
    match outcome:
        case RetryLaterOutcome():
            return True
        case (
            ResolvedOutcome()
            | WaitingOutcome()
            | NextStrategyOutcome()
            | ConfigFailureOutcome()
            | UnknownOutcome()
        ):
            return False
        case _:
            return False


__all__ = [
    "RemoteClient",
    "fast_forward_target",
    "is_retryable_fast_forward_failure",
    "retry_pending_remote_publish",
]
