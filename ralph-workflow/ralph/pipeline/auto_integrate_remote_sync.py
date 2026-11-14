from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from loguru import logger

from ralph.git import remote_push as _remote_push_module
from ralph.git.merge import branch_sha, is_ancestor
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline._publication_state_machine import PublicationDecision
from ralph.pipeline._remote_default_client import (
    _DefaultRemoteClient,
)
from ralph.pipeline.auto_integrate_budget_seam import observe_conflict_identity
from ralph.pipeline.auto_integrate_conflict_budget import (
    apply_conflict_budget,
    resolver_allowed,
)
from ralph.pipeline.auto_integrate_ff import RemoteClient
from ralph.pipeline.auto_integrate_remote_client import (
    _dispatch_pull_outcome,
    _fast_forward_local_target_to_remote,
    fast_forward_target_to_remote,
    pull_and_reconcile_target,
)
from ralph.pipeline.auto_integrate_remote_constants import (
    FETCH_TIMEOUT_SECONDS,
    REMOTE_ALREADY_CURRENT,
    REMOTE_AUTH_FAILED,
    REMOTE_BACKOFF_MAX_SECONDS,
    REMOTE_CREATED,
    REMOTE_LOCAL_AHEAD,
    REMOTE_NO_REMOTE,
    REMOTE_NO_REMOTE_BRANCH,
    REMOTE_PULL_FAILED,
    REMOTE_PULLED,
    REMOTE_PUSH_RECONCILED,
    REMOTE_PUSH_REJECTED,
    REMOTE_PUSHED,
    REMOTE_RECONCILED,
    REMOTE_REJECTED_BY_HOOK,
    REMOTE_REMOTE_UNREACHABLE,
    REMOTE_SYNC_INTERVAL_SECONDS,
    REMOTE_TIMEOUT,
    RemoteSyncStatus,
    reclaim_target_worktree_enabled,
    remote_sync_enabled,
    remote_target_name,
    remote_unreachable_reason,
)
from ralph.pipeline.auto_integrate_remote_push import (
    _post_push_publication_decision,
    _PushOutcome,
    _remote_sync_status,
    push_single_remote,
    push_target_after_landing,
    reconcile_after_rejected_push,
)
from ralph.pipeline.auto_integrate_sync import (
    REFRESH_DIVERGED,
    REFRESH_LOCAL_AHEAD,
    REFRESH_LOCAL_FLEET,
    REFRESH_NO_LOCAL_BRANCH,
    REFRESH_NO_REMOTE,
    REFRESH_NO_REMOTE_BRANCH,
    REFRESH_ORIGIN_AHEAD,
    REFRESH_SUPPRESSED,
    REFRESH_UNREACHABLE,
    refresh_target_from_remote,
)
from ralph.pipeline.integration_remote_actions import RemoteOps, default_remote_ops
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.config.models import UnifiedConfig
    from ralph.pipeline.conflict_resolution import RebaseStopResolver


__all__ = [
    "FETCH_TIMEOUT_SECONDS",
    "REMOTE_ALREADY_CURRENT",
    "REMOTE_AUTH_FAILED",
    "REMOTE_BACKOFF_MAX_SECONDS",
    "REMOTE_CREATED",
    "REMOTE_LOCAL_AHEAD",
    "REMOTE_NO_REMOTE",
    "REMOTE_NO_REMOTE_BRANCH",
    "REMOTE_PULLED",
    "REMOTE_PULL_FAILED",
    "REMOTE_PUSHED",
    "REMOTE_PUSH_RECONCILED",
    "REMOTE_PUSH_REJECTED",
    "REMOTE_RECONCILED",
    "REMOTE_REJECTED_BY_HOOK",
    "REMOTE_REMOTE_UNREACHABLE",
    "REMOTE_SYNC_INTERVAL_SECONDS",
    "REMOTE_TIMEOUT",
    "_DEFAULT_REMOTE_CLIENT",
    "_REMOTE_PULL_THROTTLE",
    "RemoteClient",
    "RemoteOps",
    "RemoteSyncStatus",
    "_DefaultRemoteClient",
    "_PushOutcome",
    "_RemotePullThrottle",
    "_dispatch_pull_outcome",
    "_fast_forward_local_target_to_remote",
    "_post_push_publication_decision",
    "_remote_push_module",
    "_remote_sync_status",
    "_throttle_allows_pull",
    "apply_conflict_budget",
    "fast_forward_target_to_remote",
    "observe_conflict_identity",
    "pull_and_reconcile_target",
    "push_single_remote",
    "push_target_after_landing",
    "reclaim_target_worktree_enabled",
    "reconcile_after_rejected_push",
    "reconcile_remote_publication",
    "refresh_observation_record",
    "refresh_target_from_remote",
    "remote_sync_enabled",
    "remote_target_name",
    "remote_unreachable_reason",
    "resolver_allowed",
]


_DEFAULT_REMOTE_CLIENT: RemoteOps = default_remote_ops()


_MAX_REMOTE_KEYS = 128


class _RemotePullThrottle:
    def __init__(self, *, max_tracked_keys: int = _MAX_REMOTE_KEYS) -> None:
        self._max_tracked_keys = max(1, max_tracked_keys)
        self._last_pull: OrderedDict[tuple[str, str, str], float] = OrderedDict()

    def should_pull(
        self,
        root: Path | str,
        remote: str,
        target: str,
        *,
        interval_seconds: float,
        clock: Callable[[], float],
    ) -> bool:
        key = self._key(root, remote, target)
        last = self._last_pull.get(key)
        if last is None:
            return True
        if interval_seconds <= 0.0:
            return True
        return clock() - last >= interval_seconds

    def arm(self, root: Path | str, remote: str, target: str, clock: Callable[[], float]) -> None:
        key = self._key(root, remote, target)
        self._last_pull.pop(key, None)
        while len(self._last_pull) >= self._max_tracked_keys:
            self._last_pull.popitem(last=False)
        self._last_pull[key] = clock()

    def invalidate(self, root: Path | str, remote: str, target: str) -> None:
        self._last_pull.pop(self._key(root, remote, target), None)

    @staticmethod
    def _key(root: Path | str, remote: str, target: str) -> tuple[str, str, str]:
        return (str(root), remote, target)


_REMOTE_PULL_THROTTLE = _RemotePullThrottle()


def _throttle_allows_pull(
    repo_root: Path,
    remote: str,
    target: str,
    config: UnifiedConfig | None,
    clock: Callable[[], float],
) -> bool:
    general: object = getattr(config, "general", config)
    configured: object = getattr(general, "auto_integrate_remote_interval_seconds", 0.0)
    interval = configured if isinstance(configured, (int, float)) else REMOTE_SYNC_INTERVAL_SECONDS
    return _REMOTE_PULL_THROTTLE.should_pull(
        repo_root,
        remote,
        target,
        interval_seconds=interval,
        clock=clock,
    )


_READABLE_REFRESH_OUTCOMES: frozenset[str] = frozenset(
    {
        REFRESH_LOCAL_FLEET,
        REFRESH_LOCAL_AHEAD,
        REFRESH_DIVERGED,
        REFRESH_NO_REMOTE,
        REFRESH_NO_LOCAL_BRANCH,
        REFRESH_ORIGIN_AHEAD,
        REFRESH_SUPPRESSED,
        "refreshed",
        "already current",
        "refresh suppressed",
        "refresh suppressed by throttle",
    }
)

_STALE_REFRESH_OUTCOMES: frozenset[str] = frozenset(
    {REFRESH_SUPPRESSED, "refresh suppressed", "refresh suppressed by throttle"}
)


def _read_remote_tracking_sha(
    client: RemoteOps,
    repo_root: Path,
    target: str,
    remote: str,
) -> str | None:
    if hasattr(client, "observe_remote_sha"):
        return client.observe_remote_sha(repo_root, target, remote=remote)
    from ralph.pipeline.auto_integrate_catchup import observe_remote_target_sha

    return observe_remote_target_sha(repo_root, remote, target)


def _throttled_reading_needs_refetch(
    observed: str | None,
    local_sha: str | None,
) -> bool:
    if observed is None:
        return True
    return local_sha is None or observed == local_sha


def _observe_remote_target_sha(
    repo_root: Path,
    target: str,
    *,
    remote: str,
    remote_client: RemoteOps | None = None,
    local_sha: str | None = None,
) -> tuple[str | None, str]:
    client = remote_client or _DEFAULT_REMOTE_CLIENT
    try:
        refresh = client.refresh_target(
            repo_root,
            target,
            timeout_seconds=FETCH_TIMEOUT_SECONDS,
            remote=remote,
        )
        if refresh == REFRESH_NO_REMOTE_BRANCH:
            return None, REFRESH_NO_REMOTE_BRANCH
        if refresh not in _READABLE_REFRESH_OUTCOMES:
            return None, refresh
        observed = _read_remote_tracking_sha(client, repo_root, target, remote)
        if refresh in _STALE_REFRESH_OUTCOMES and _throttled_reading_needs_refetch(
            observed, local_sha
        ):
            forced = refresh_target_from_remote(
                repo_root,
                target,
                timeout_seconds=FETCH_TIMEOUT_SECONDS,
                remote=remote,
            )
            observed = _read_remote_tracking_sha(client, repo_root, target, remote)
            if forced in _READABLE_REFRESH_OUTCOMES:
                refresh = forced
        return observed, refresh
    except Exception as exc:
        logger.debug("auto_integrate: remote observation failed: {}", exc)
        return None, REFRESH_UNREACHABLE


def _record_publication_failure(
    base_state: RebaseState,
    *,
    last_remote_sync: str,
    last_reason: str | None = None,
    last_refresh: str | None = None,
    fast_forwarded: bool | None = None,
    publication_decision: PublicationDecision | None = None,
) -> RebaseState:
    update: dict[str, object] = {
        "last_remote_sync": last_remote_sync,
    }
    if last_reason is not None:
        update["last_reason"] = last_reason
    if last_refresh is not None:
        update["last_refresh"] = last_refresh
    if fast_forwarded is not None:
        update["fast_forwarded"] = fast_forwarded
    new_state = base_state.model_copy(update=update)
    if publication_decision is not None:
        new_state = new_state.set_publication_decision(publication_decision)
    return new_state


@runtime_checkable
class _OutcomeWithSucceeded(Protocol):
    succeeded: bool


def _dispatch_ref_comparison(
    *,
    config: UnifiedConfig | None,
    repo_root: Path,
    target: str,
    chosen_remote: str,
    local_sha: str,
    remote_sha: str,
    base_state: RebaseState,
    remote_client: RemoteOps | None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
) -> RebaseState:
    try:
        local_ahead = is_ancestor(repo_root, remote_sha, local_sha)
    except Exception:
        local_ahead = False
    try:
        remote_ahead = is_ancestor(repo_root, local_sha, remote_sha)
    except Exception:
        remote_ahead = False
    if local_ahead and not remote_ahead:
        pushed = push_target_after_landing(
            config,
            repo_root,
            target,
            base_state.model_copy(update={"fast_forwarded": True}),
            remote=chosen_remote,
            remote_client=remote_client,
        )
        if pushed.last_push_status == _remote_push_module.PushStatus.NON_FAST_FORWARD.value:
            return reconcile_after_rejected_push(
                config,
                repo_root,
                target,
                pushed,
                remote=chosen_remote,
                rebase_stop_resolver=rebase_stop_resolver,
                remote_client=remote_client,
            )
        return pushed
    if remote_ahead and not local_ahead:
        if config is None:
            return _record_publication_failure(
                base_state,
                last_remote_sync=REMOTE_PULL_FAILED,
                last_refresh=REFRESH_UNREACHABLE,
                publication_decision=PublicationDecision.PUBLISH_DEFERRED_REMOTE,
            )
        raw_outcome: object = fast_forward_target_to_remote(
            repo_root,
            target,
            chosen_remote,
            reclaim_target_worktree=False,
            remote_sha=remote_sha,
        )
        from ralph.pipeline.resolution_outcome import (
            ResolvedOutcome,
            RetryLaterOutcome,
            WaitingOutcome,
        )

        if isinstance(raw_outcome, tuple):
            advanced = bool(raw_outcome[0])
            refusal = str(raw_outcome[1]) if len(raw_outcome) > 1 else ""
        elif isinstance(raw_outcome, bool):
            advanced, refusal = raw_outcome, "" if raw_outcome else "fast forward failed"
        elif isinstance(raw_outcome, ResolvedOutcome):
            advanced, refusal = True, ""
        elif isinstance(raw_outcome, RetryLaterOutcome):
            advanced, refusal = False, raw_outcome.on_change or ""
        elif isinstance(raw_outcome, WaitingOutcome):
            advanced, refusal = False, raw_outcome.external_condition
        elif isinstance(raw_outcome, _OutcomeWithSucceeded):
            advanced = bool(raw_outcome.succeeded)
            on_change: object = getattr(raw_outcome, "on_change", None)
            reason: object = getattr(raw_outcome, "reason", "")
            refusal = str(on_change or reason or "")
        else:
            advanced, refusal = False, str(raw_outcome)
        if advanced:
            return reconcile_remote_publication(
                config,
                repo_root,
                target,
                remote=chosen_remote,
                remote_client=remote_client,
                rebase_stop_resolver=rebase_stop_resolver,
                source_state=base_state,
            )
        return _record_publication_failure(
            base_state,
            last_remote_sync=REMOTE_PULL_FAILED,
            last_reason=refusal,
            last_refresh=REFRESH_ORIGIN_AHEAD,
            publication_decision=PublicationDecision.PUBLISH_DEFERRED_REMOTE,
        )
    return reconcile_after_rejected_push(
        config,
        repo_root,
        target,
        base_state,
        remote=chosen_remote,
        rebase_stop_resolver=rebase_stop_resolver,
        remote_client=remote_client,
    )


def _build_disabled_noop_state(
    *,
    source_state: RebaseState | None,
    target: str,
    remote: str | None,
    config: UnifiedConfig | None,
) -> RebaseState:
    chosen_remote = (
        remote
        if isinstance(remote, str) and remote
        else (remote_target_name(config) if config is not None else None)
    )
    if source_state is not None:
        return source_state.model_copy(
            update={
                "last_target": target,
                "last_remote": chosen_remote,
                "last_remote_sync": REMOTE_NO_REMOTE,
                "last_reason": "remote sync disabled by policy",
            }
        )
    return RebaseState(
        last_outcome=RebaseOutcomeKind.SKIPPED,
        last_target=target,
        last_remote=chosen_remote,
        last_remote_sync=REMOTE_NO_REMOTE,
        last_reason="remote sync disabled by policy",
        fast_forwarded=False,
        freshness_verdict="verified",
    )


def _safe_local_sha(repo_root: Path, target: str) -> str | None:
    try:
        return branch_sha(repo_root, target)
    except Exception as exc:
        logger.debug("auto_integrate: local target SHA unreadable: {}", exc)
        return None


def _handle_no_remote_branch(
    *,
    config: UnifiedConfig | None,
    repo_root: Path,
    target: str,
    base_state: RebaseState,
    chosen_remote: str,
    client: RemoteOps,
    rebase_stop_resolver: RebaseStopResolver | None,
) -> RebaseState:
    pushed = push_target_after_landing(
        config,
        repo_root,
        target,
        base_state.model_copy(update={"fast_forwarded": True}),
        remote=chosen_remote,
        remote_client=client,
    )
    if pushed.last_push_status == _remote_push_module.PushStatus.NON_FAST_FORWARD.value:
        return reconcile_after_rejected_push(
            config,
            repo_root,
            target,
            pushed,
            remote=chosen_remote,
            rebase_stop_resolver=rebase_stop_resolver,
            remote_client=client,
        )
    return pushed


def reconcile_remote_publication(
    config: UnifiedConfig | None,
    repo_root: Path,
    target: str,
    *,
    remote: str | None = None,
    remote_client: RemoteOps | None = None,
    rebase_stop_resolver: RebaseStopResolver | None = None,
    source_state: RebaseState | None = None,
) -> RebaseState:
    if not remote_sync_enabled(config):
        return _build_disabled_noop_state(
            source_state=source_state,
            target=target,
            remote=remote,
            config=config,
        )
    chosen_remote = remote if isinstance(remote, str) and remote else remote_target_name(config)
    base_state: RebaseState
    if source_state is not None:
        base_state = source_state.model_copy(
            update={
                "last_target": target,
                "last_remote": chosen_remote,
                "freshness_verdict": "verified",
            }
        )
    else:
        base_state = RebaseState(
            last_outcome=RebaseOutcomeKind.SKIPPED,
            last_target=target,
            last_remote=chosen_remote,
            fast_forwarded=False,
            freshness_verdict="verified",
        )
    local_sha = _safe_local_sha(repo_root, target)
    if local_sha is None:
        return _record_publication_failure(
            base_state,
            last_remote_sync=REMOTE_PULL_FAILED,
        )
    client = remote_client or _DEFAULT_REMOTE_CLIENT
    remote_sha, refresh = _observe_remote_target_sha(
        repo_root,
        target,
        remote=chosen_remote,
        remote_client=client,
        local_sha=local_sha,
    )
    if refresh == REFRESH_NO_REMOTE_BRANCH:
        return _handle_no_remote_branch(
            config=config,
            repo_root=repo_root,
            target=target,
            base_state=base_state,
            chosen_remote=chosen_remote,
            client=client,
            rebase_stop_resolver=rebase_stop_resolver,
        )
    if remote_sha is None:
        return _record_publication_failure(
            base_state,
            last_remote_sync=REMOTE_REMOTE_UNREACHABLE,
            last_reason=remote_unreachable_reason(chosen_remote),
            last_refresh=refresh,
            publication_decision=PublicationDecision.PUBLISH_DEFERRED_REMOTE,
        )
    if local_sha == remote_sha:
        return _record_publication_failure(
            base_state,
            last_remote_sync=REMOTE_ALREADY_CURRENT,
            last_reason="local and remote already converge",
            fast_forwarded=True,
        )
    return _dispatch_ref_comparison(
        config=config,
        repo_root=repo_root,
        target=target,
        chosen_remote=chosen_remote,
        local_sha=local_sha,
        remote_sha=remote_sha,
        base_state=base_state,
        remote_client=client,
        rebase_stop_resolver=rebase_stop_resolver,
    )


def _observation_reconciles(
    *,
    repo_root: Path,
    target: str,
    remote: str,
    local_sha: str,
    remote_sha: str | None,
) -> bool:
    if remote_sha is None:
        return False
    if local_sha != remote_sha:
        return False
    _ = (repo_root, target, remote)
    return True


def refresh_observation_record(
    repo_root: Path,
    base_state: RebaseState,
    *,
    target: str,
    remote: str,
    remote_client: RemoteOps | None = None,
) -> RebaseState:
    if not isinstance(target, str) or not target or not isinstance(remote, str) or not remote:
        return base_state
    local_sha = _safe_local_sha(repo_root, target)
    if local_sha is None:
        return base_state
    client = remote_client or _DEFAULT_REMOTE_CLIENT
    remote_sha, refresh = _observe_remote_target_sha(
        repo_root,
        target,
        remote=remote,
        remote_client=client,
        local_sha=local_sha,
    )
    if remote_sha is not None and _observation_reconciles(
        repo_root=repo_root,
        target=target,
        remote=remote,
        local_sha=local_sha,
        remote_sha=remote_sha,
    ):
        return _record_publication_failure(
            base_state,
            last_remote_sync=REMOTE_ALREADY_CURRENT,
            last_reason=_render_converged_reason(target, remote),
            fast_forwarded=True,
            publication_decision=PublicationDecision.PUBLICATION_TERMINAL,
        )
    if remote_sha is None:
        return _record_publication_failure(
            base_state,
            last_remote_sync=REMOTE_REMOTE_UNREACHABLE,
            last_reason=remote_unreachable_reason(remote),
            last_refresh=refresh,
            publication_decision=PublicationDecision.PUBLISH_DEFERRED_REMOTE,
        )
    if refresh == REFRESH_NO_REMOTE_BRANCH:
        return base_state.model_copy(
            update={
                "last_remote": remote,
                "last_remote_sync": REMOTE_NO_REMOTE_BRANCH,
                "last_reason": _render_missing_branch_reason(target, remote),
            }
        ).set_publication_decision(
            PublicationDecision.PUBLISH_DEFERRED_REMOTE,
        )
    return base_state.model_copy(
        update={
            "last_remote": remote,
            "last_remote_sync": REMOTE_LOCAL_AHEAD,
            "last_refresh": refresh,
            "last_reason": _render_unpublished_reason(target, remote),
        }
    ).set_publication_decision(PublicationDecision.PUBLISH_DEFERRED_REMOTE)


def _render_converged_reason(target: str, remote: str) -> str:
    return f"local {target} and {remote}/{target} converge"


def _render_unpublished_reason(target: str, remote: str) -> str:
    return f"local {target} is not yet published to {remote}/{target}"


def _render_missing_branch_reason(target: str, remote: str) -> str:
    return f"{remote}/{target} does not exist yet; publication will create it"
