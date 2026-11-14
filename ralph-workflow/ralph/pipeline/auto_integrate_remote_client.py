from __future__ import annotations

import time
from typing import TYPE_CHECKING

from loguru import logger

from ralph.git import remote_push as _remote_push_module
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline.auto_integrate_conflict_budget import (
    ConflictIdentity,
)
from ralph.pipeline.auto_integrate_remote_constants import (
    FETCH_TIMEOUT_SECONDS,
    REMOTE_LOCAL_AHEAD,
    REMOTE_NO_REMOTE,
    REMOTE_NO_REMOTE_BRANCH,
    REMOTE_PULL_FAILED,
    REMOTE_PULLED,
    REMOTE_RECONCILED,
    reclaim_target_worktree_enabled,
    remote_sync_enabled,
    remote_target_name,
    remote_unreachable_reason,
)
from ralph.pipeline.auto_integrate_sync import (
    REFRESH_DIVERGED,
    REFRESH_LOCAL_AHEAD,
    REFRESH_LOCAL_FLEET,
    REFRESH_NO_LOCAL_BRANCH,
    REFRESH_NO_REMOTE,
    REFRESH_NO_REMOTE_BRANCH,
    REFRESH_ORIGIN_AHEAD,
    REFRESH_UNREACHABLE,
    refresh_target_from_remote,
)
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.auto_integrate_ff import RemoteClient
    from ralph.pipeline.conflict_resolution import RebaseStopResolver
    from ralph.pipeline.resolution_outcome import StopResolutionOutcome
    from ralph.recovery.agent_failure_ledger import AgentFailureLedger


__all__ = [
    "_dispatch_pull_outcome",
    "fast_forward_target_to_remote",
    "pull_and_reconcile_target",
    "refresh_target_from_remote",
]


class _DefaultRemoteClient:
    def refresh_target(
        self,
        repo_root: Path,
        target: str,
        *,
        timeout_seconds: float,
        remote: str,
    ) -> str:
        try:
            import ralph.pipeline.auto_integrate_remote_sync as _remote_sync

            return _remote_sync.refresh_target_from_remote(
                repo_root,
                target,
                timeout_seconds=timeout_seconds,
                remote=remote,
            )
        except Exception as exc:
            logger.debug("auto_integrate: remote refresh query failed: {}", exc)
            return REFRESH_UNREACHABLE

    def push_branch(
        self,
        repo_root: Path,
        branch: str,
        *,
        remote: str,
        timeout_seconds: float,
    ) -> _remote_push_module.PushResult:
        try:
            return _remote_push_module.push_branch_to_single_remote(
                repo_root,
                branch,
                remote=remote,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            logger.debug("auto_integrate: remote push failed: {}", exc)
            return _remote_push_module.PushResult(
                _remote_push_module.PushStatus.UNREACHABLE,
                remote,
                branch,
                detail=str(exc),
            )

    def observe_remote_sha(
        self,
        repo_root: Path,
        target: str,
        *,
        remote: str,
    ) -> str | None:
        return _remote_tracking_sha(repo_root, target, remote)


_DEFAULT_REMOTE_CLIENT = _DefaultRemoteClient()


def _resolve_effective_ledger(
    ledger: AgentFailureLedger | None,
) -> AgentFailureLedger | None:
    from ralph.pipeline.auto_integrate_conflict_budget import resolve_failure_ledger

    return resolve_failure_ledger(ledger)


def _prepare_effective_resolver(
    rebase_stop_resolver: RebaseStopResolver | None,
    prior_state: RebaseState,
    target: str,
    identity: ConflictIdentity,
    ledger: AgentFailureLedger | None,
    candidates: Sequence[str],
) -> RebaseStopResolver | None:
    if rebase_stop_resolver is None:
        return None
    from ralph.pipeline.auto_integrate_remote_sync import (
        resolver_allowed as _resolver_allowed,
    )

    budget_allows = _resolver_allowed(prior_state, target, identity)
    eligibility_allows = ledger is None or ledger.any_eligible(
        "rebase_conflict_resolution", candidates
    )
    if not budget_allows or not eligibility_allows:
        return None
    return rebase_stop_resolver


def pull_and_reconcile_target(
    config: UnifiedConfig | None,
    repo_root: Path,
    target: str,
    *,
    remote: str | None = None,
    clock: Callable[[], float] = time.monotonic,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    prior: RebaseState | None = None,
    remote_client: RemoteClient | None = None,
    ledger: AgentFailureLedger | None = None,
    candidates: Sequence[str] = (),
) -> RebaseState | None:
    if config is None or not remote_sync_enabled(config):
        return None

    client = remote_client or _DEFAULT_REMOTE_CLIENT
    chosen_remote = remote if isinstance(remote, str) and remote else remote_target_name(config)
    try:
        from ralph.pipeline.auto_integrate_remote_sync import observe_conflict_identity

        identity = observe_conflict_identity(repo_root, target, scope="remote")
    except Exception:
        identity = ConflictIdentity(scope="remote")
    prior_state = prior or RebaseState()
    effective_ledger = _resolve_effective_ledger(ledger)
    effective_resolver = _prepare_effective_resolver(
        rebase_stop_resolver,
        prior_state,
        target,
        identity,
        effective_ledger,
        candidates,
    )
    import ralph.pipeline.auto_integrate_remote_sync as _remote_sync

    if not _remote_sync._throttle_allows_pull(repo_root, chosen_remote, target, config, clock):
        state = _record_remote_state(
            target,
            last_remote_sync="refresh suppressed",
            last_refresh="refresh suppressed by throttle",
            reason="remote freshness probe suppressed by configured interval",
            freshness_verdict="unverified",
            freshness_source="suppressed probe",
        )
    else:
        refresh = client.refresh_target(
            repo_root,
            target,
            timeout_seconds=FETCH_TIMEOUT_SECONDS,
            remote=chosen_remote,
        )
        pulled = _remote_sync._dispatch_pull_outcome(
            refresh, repo_root, target, chosen_remote, clock, config, effective_resolver
        )
        if pulled is None:
            state = prior_state
        else:
            from ralph.git.merge import branch_sha

            try:
                target_sha = branch_sha(repo_root, target)
            except OSError:
                target_sha = None
            state = pulled.model_copy(
                update={"last_remote": chosen_remote, "freshness_target_sha": target_sha}
            )
    from ralph.pipeline.auto_integrate_remote_sync import apply_conflict_budget

    return apply_conflict_budget(
        state,
        prior=prior_state,
        target=target,
        resolver_suppressed=rebase_stop_resolver is not None and effective_resolver is None,
        identity=identity,
    )


def _dispatch_pull_outcome(
    refresh: str,
    repo_root: Path,
    target: str,
    chosen_remote: str,
    clock: Callable[[], float],
    config: UnifiedConfig | None,
    rebase_stop_resolver: RebaseStopResolver | None,
) -> RebaseState | None:
    state: RebaseState | None = None
    if refresh == REFRESH_LOCAL_FLEET:
        _arm_throttle(repo_root, chosen_remote, target, clock)
        state = _record_remote_state(
            target,
            last_remote_sync="local fleet",
            last_refresh=refresh,
            reason=None,
            freshness_verdict="verified",
            freshness_source="shared local ref",
        )
    elif refresh == REFRESH_UNREACHABLE:
        logger.debug("auto_integrate: remote sync pull unreachable on '{}'", target)
        _consume_throttle_signal_unhealthy(repo_root, chosen_remote, target)
        state = _record_remote_state(
            target,
            last_remote_sync=REMOTE_PULL_FAILED,
            last_refresh=refresh,
            reason=remote_unreachable_reason(chosen_remote),
            freshness_verdict="degraded",
            freshness_source="fetch",
        )
    elif refresh in (REFRESH_NO_REMOTE, REFRESH_NO_REMOTE_BRANCH):
        _consume_throttle_signal_unhealthy(repo_root, chosen_remote, target)
        state = _record_remote_state(
            target,
            last_remote_sync=(
                REMOTE_NO_REMOTE_BRANCH if refresh == REFRESH_NO_REMOTE_BRANCH else REMOTE_NO_REMOTE
            ),
            last_refresh=refresh,
            reason=None,
            freshness_verdict="verified" if refresh == REFRESH_NO_REMOTE else "degraded",
            freshness_source="shared local ref" if refresh == REFRESH_NO_REMOTE else "fetch",
        )
    elif refresh == REFRESH_NO_LOCAL_BRANCH:
        _consume_throttle_signal_unhealthy(repo_root, chosen_remote, target)
    elif refresh == REFRESH_ORIGIN_AHEAD:
        try:
            import ralph.pipeline.auto_integrate_remote_sync as _remote_sync

            _remote_sync._fast_forward_local_target_to_remote(
                repo_root, target, chosen_remote, config
            )
        except Exception as exc:
            logger.warning("auto_integrate: pull-side ff failed: {}", exc)
            _consume_throttle_signal_unhealthy(repo_root, chosen_remote, target)
            state = _record_remote_state(
                target,
                last_remote_sync=REMOTE_PULL_FAILED,
                last_refresh=refresh,
                reason=str(exc),
                freshness_verdict="unsafe",
                freshness_source="fetch",
                freshness_safe=False,
            )
        else:
            _arm_throttle(repo_root, chosen_remote, target, clock)
            state = _record_remote_state(
                target,
                last_remote_sync=REMOTE_PULLED,
                last_refresh=refresh,
                reason=None,
                freshness_verdict="verified",
                freshness_source="fetch",
            )
    elif refresh == REFRESH_LOCAL_AHEAD:
        _arm_throttle(repo_root, chosen_remote, target, clock)
        state = _record_remote_state(
            target,
            last_remote_sync=REMOTE_LOCAL_AHEAD,
            last_refresh=refresh,
            reason=None,
            freshness_verdict="verified",
            freshness_source="fetch",
        )
    elif refresh == REFRESH_DIVERGED:
        from ralph.pipeline.auto_integrate_remote_reconcile import reconcile_target_onto_remote

        reconciliation = reconcile_target_onto_remote(
            repo_root,
            target,
            chosen_remote,
            reclaim_target_worktree=reclaim_target_worktree_enabled(config),
            rebase_stop_resolver=rebase_stop_resolver,
            conflict_resolution_config=config,
        )
        if reconciliation.reconciled:
            _arm_throttle(repo_root, chosen_remote, target, clock)
            state = _record_remote_state(
                target,
                last_remote_sync=REMOTE_RECONCILED,
                last_refresh=refresh,
                reason=None,
                freshness_verdict="verified",
                freshness_source="fetch",
            )
        else:
            _consume_throttle_signal_unhealthy(repo_root, chosen_remote, target)
            state = _record_remote_state(
                target,
                last_remote_sync=REMOTE_PULL_FAILED,
                last_refresh=refresh,
                reason=reconciliation.reason,
                freshness_verdict="degraded" if reconciliation.cleanly_aborted else "unsafe",
                freshness_source="fetch",
                freshness_safe=reconciliation.cleanly_aborted,
            )
    else:
        _arm_throttle(repo_root, chosen_remote, target, clock)
        state = _record_remote_state(
            target,
            last_remote_sync="already current",
            last_refresh=refresh,
            reason=None,
            freshness_verdict="verified",
            freshness_source="fetch",
        )
    return state


def _fast_forward_local_target_to_remote(
    repo_root: Path,
    target: str,
    remote: str,
    config: UnifiedConfig | None,
) -> None:
    outcome: object = fast_forward_target_to_remote(
        repo_root,
        target,
        remote,
        reclaim_target_worktree=reclaim_target_worktree_enabled(config),
    )
    from ralph.pipeline.resolution_outcome import ResolvedOutcome

    if isinstance(outcome, tuple):
        if not outcome[0]:
            raise RuntimeError(outcome[1] if len(outcome) > 1 else str(outcome))
        return
    if not isinstance(outcome, ResolvedOutcome):
        raise RuntimeError(str(outcome))


def fast_forward_target_to_remote(
    repo_root: Path,
    target: str,
    remote: str,
    *,
    reclaim_target_worktree: bool,
    remote_sha: str | None = None,
) -> StopResolutionOutcome:
    from ralph.git.merge import branch_sha, is_ancestor
    from ralph.pipeline.auto_integrate_ff import fast_forward_target
    from ralph.pipeline.resolution_outcome import (
        ResolvedOutcome,
        RetryLaterOutcome,
        UnknownOutcome,
    )

    observed = remote_sha or _remote_tracking_sha(repo_root, target, remote)
    if observed is None:
        return UnknownOutcome(RuntimeError(f"{remote}/{target} missing for ff"))
    local_sha = branch_sha(repo_root, target)
    if local_sha is None:
        return UnknownOutcome(RuntimeError(f"local {target} missing for ff"))
    if local_sha == observed:
        return ResolvedOutcome()
    if not is_ancestor(repo_root, local_sha, observed):
        return RetryLaterOutcome(on_change=f"local/{target} diverged from {remote}/{target}")
    return fast_forward_target(
        repo_root,
        target,
        observed,
        reclaim_target_worktree=reclaim_target_worktree,
    )


def _remote_tracking_sha(repo_root: Path, target: str, remote: str) -> str | None:
    from ralph.git.subprocess_runner import run_git

    result = run_git(
        ("rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}/{target}"),
        cwd=repo_root,
        label=f"git-remote-tracking-sha-pull-{remote}",
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _arm_throttle(
    repo_root: Path,
    remote: str,
    target: str,
    clock: Callable[[], float],
) -> None:
    from ralph.pipeline.auto_integrate_remote_sync import _REMOTE_PULL_THROTTLE

    _REMOTE_PULL_THROTTLE.arm(repo_root, remote, target, clock)


def _consume_throttle_signal_unhealthy(repo_root: Path, remote: str, target: str) -> None:
    from ralph.pipeline.auto_integrate_remote_sync import _REMOTE_PULL_THROTTLE

    _REMOTE_PULL_THROTTLE.invalidate(repo_root, remote, target)


def _record_remote_state(
    target: str,
    *,
    last_remote_sync: str,
    last_refresh: str | None,
    reason: str | None,
    freshness_verdict: str | None = None,
    freshness_source: str | None = None,
    freshness_safe: bool = True,
) -> RebaseState:
    return RebaseState(
        last_outcome=RebaseOutcomeKind.SKIPPED,
        last_reason=reason,
        last_target=target,
        last_remote_sync=last_remote_sync,
        last_refresh=last_refresh,
        freshness_verdict=freshness_verdict,
        freshness_source=freshness_source,
        freshness_safe=freshness_safe,
    )
