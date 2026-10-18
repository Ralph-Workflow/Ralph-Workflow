"""Constants and policy models for auto-integrate remote synchronization.

Extracted so ``auto_integrate_remote_client`` and
``auto_integrate_remote_push`` can import constants without a circular import
with ``auto_integrate_remote_sync``.
"""

from __future__ import annotations

from enum import StrEnum

from ralph.config.models import UnifiedConfig
from ralph.pipeline.auto_integrate_conflict_budget import MAX_CONSECUTIVE_RESOLVER_ATTEMPTS
from ralph.pipeline.auto_integrate_sync import DEFAULT_REFRESH_REMOTE


class RemoteSyncStatus(StrEnum):
    """Closed vocabulary for remote synchronization outcomes."""

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
    REMOTE_UNREACHABLE = "origin unreachable"
    AUTH_FAILED = "auth failed"
    REJECTED_BY_HOOK = "rejected by remote hook"
    NON_FAST_FORWARD = "non-fast-forward"
    TIMEOUT = "timeout"


#: Public outcome string aliases carried on RebaseState.last_remote_sync.
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

#: Per-seam remote reconcile cap; later seams always retry.
_MAX_REMOTE_SYNC_ATTEMPTS = 3


def remote_sync_enabled(config: object | None) -> bool:
    """Whether configured-remote synchronization is enabled by policy.

    Resolves ``auto_integrate_remote_enabled`` when explicitly set;
    otherwise falls back to the top-level ``auto_integrate_enabled``
    flag so callers that disable the integration seam by setting
    only ``auto_integrate_enabled=False`` (the established test
    convention) also disable remote sync without having to repeat
    the inverse. An explicit ``auto_integrate_remote_enabled``
    always wins.
    """
    if config is None:
        return False
    general: object = getattr(config, "general", config)
    fields_set: object = getattr(general, "model_fields_set", None)
    if isinstance(fields_set, (set, frozenset)) and "auto_integrate_remote_enabled" in fields_set:
        remote_enabled: object = getattr(general, "auto_integrate_remote_enabled", True)
        return isinstance(remote_enabled, bool) and remote_enabled
    general_enabled: object = getattr(general, "auto_integrate_enabled", True)
    return isinstance(general_enabled, bool) and general_enabled


def remote_target_name(config: object, *, default: str = DEFAULT_REFRESH_REMOTE) -> str:
    """Configured remote name, or the default ``origin``."""
    general: object = getattr(config, "general", config)
    remote: object = getattr(general, "auto_integrate_remote", default)
    return remote.strip() if isinstance(remote, str) and remote.strip() else default


def resolver_attempt_limit(config: object) -> int:
    """Return the configured budget, retaining the public GeneralConfig seam."""
    if isinstance(config, UnifiedConfig):
        return config.conflict_resolution.max_consecutive_resolver_attempts
    conflict_resolution: object = getattr(config, "conflict_resolution", None)
    if conflict_resolution is not None:
        attempts: object = getattr(conflict_resolution, "max_consecutive_resolver_attempts", None)
        if isinstance(attempts, int) and attempts > 0:
            return attempts
    return MAX_CONSECUTIVE_RESOLVER_ATTEMPTS


def reclaim_target_worktree_enabled(config: object | None) -> bool:
    """Return the target-owner reclamation policy, defaulting safely to enabled."""
    general: object = getattr(config, "general", config)
    configured: object = getattr(general, "auto_integrate_reclaim_target_worktree", True)
    return configured is not False
