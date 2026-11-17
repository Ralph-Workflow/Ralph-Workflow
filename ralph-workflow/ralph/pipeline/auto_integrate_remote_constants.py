from __future__ import annotations

from enum import StrEnum

from ralph.pipeline.auto_integrate_sync import DEFAULT_REFRESH_REMOTE


class RemoteSyncStatus(StrEnum):
    PULLED = "pulled"
    RECONCILED = "reconciled"
    CREATED = "created"
    PUSHED = "pushed"
    PUSH_RECONCILED = "push reconciled"
    PUSH_REJECTED = "push rejected"
    PULL_FAILED = "pull failed"
    NO_REMOTE = "no remote"
    NO_REMOTE_BRANCH = "no remote branch"
    LOCAL_AHEAD = "local ahead"
    ALREADY_CURRENT = "already current"
    REMOTE_UNREACHABLE = "remote unreachable"
    AUTH_FAILED = "auth failed"
    REJECTED_BY_HOOK = "rejected by remote hook"
    NON_FAST_FORWARD = "non-fast-forward"
    TIMEOUT = "timeout"


REMOTE_PULLED = RemoteSyncStatus.PULLED
REMOTE_RECONCILED = RemoteSyncStatus.RECONCILED
REMOTE_CREATED = RemoteSyncStatus.CREATED
REMOTE_PUSHED = RemoteSyncStatus.PUSHED
REMOTE_PUSH_RECONCILED = RemoteSyncStatus.PUSH_RECONCILED
REMOTE_PUSH_REJECTED = RemoteSyncStatus.PUSH_REJECTED
REMOTE_PULL_FAILED = RemoteSyncStatus.PULL_FAILED
REMOTE_NO_REMOTE = RemoteSyncStatus.NO_REMOTE
REMOTE_NO_REMOTE_BRANCH = RemoteSyncStatus.NO_REMOTE_BRANCH
REMOTE_LOCAL_AHEAD = RemoteSyncStatus.LOCAL_AHEAD
REMOTE_ALREADY_CURRENT = RemoteSyncStatus.ALREADY_CURRENT
REMOTE_REMOTE_UNREACHABLE = RemoteSyncStatus.REMOTE_UNREACHABLE
REMOTE_AUTH_FAILED = RemoteSyncStatus.AUTH_FAILED
REMOTE_REJECTED_BY_HOOK = RemoteSyncStatus.REJECTED_BY_HOOK
REMOTE_NON_FAST_FORWARD = RemoteSyncStatus.NON_FAST_FORWARD
REMOTE_TIMEOUT = RemoteSyncStatus.TIMEOUT

FETCH_TIMEOUT_SECONDS = 10.0
REMOTE_SYNC_INTERVAL_SECONDS = 300.0
REMOTE_BACKOFF_MAX_SECONDS = 300.0

_MAX_REMOTE_SYNC_ATTEMPTS = 3


def remote_unreachable_reason(remote: str) -> str:
    return f"remote {remote} unreachable; recovery will retry"


def _optional_attr(obj: object, name: str, default: object = None) -> object:
    try:
        value: object = object.__getattribute__(obj, name)
    except AttributeError:
        return default
    return value


def remote_sync_enabled(config: object | None) -> bool:
    if config is None:
        return False
    general: object = getattr(config, "general", config)
    fields_set: object = getattr(general, "model_fields_set", None)
    if isinstance(fields_set, (set, frozenset)) and "auto_integrate_remote_enabled" in fields_set:
        remote_enabled: object = getattr(general, "auto_integrate_remote_enabled", True)
        return isinstance(remote_enabled, bool) and remote_enabled
    general_enabled: object = _optional_attr(general, "auto_integrate_enabled", default=True)
    return isinstance(general_enabled, bool) and general_enabled


def remote_target_name(config: object, *, default: str = DEFAULT_REFRESH_REMOTE) -> str:
    general: object = _optional_attr(config, "general", config)
    remote: object = _optional_attr(general, "auto_integrate_remote", default)
    return remote.strip() if isinstance(remote, str) and remote.strip() else default


def reclaim_target_worktree_enabled(config: object | None) -> bool:
    if config is None:
        return True
    general: object = _optional_attr(config, "general", config)
    configured: object = _optional_attr(
        general, "auto_integrate_reclaim_target_worktree", default=True
    )
    return configured is not False
