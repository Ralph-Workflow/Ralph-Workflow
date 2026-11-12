from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.auto_integrate_sync import (
    REFRESH_ALREADY_CURRENT,
    REFRESH_DISABLED,
    REFRESH_LOCAL_FLEET,
    REFRESH_NO_REMOTE_BRANCH,
    REFRESH_ORIGIN_AHEAD,
    REFRESH_REFRESHED,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.rebase_state import RebaseState

_HEALTHY_REFRESH_OUTCOMES: frozenset[str] = frozenset(
    {
        REFRESH_ALREADY_CURRENT,
        REFRESH_DISABLED,
        REFRESH_LOCAL_FLEET,
        REFRESH_NO_REMOTE_BRANCH,
        REFRESH_ORIGIN_AHEAD,
        REFRESH_REFRESHED,
    }
)

__all__ = [
    "current_branch_or_detached_marker",
    "record_refresh",
    "record_when_stale",
    "refresh_outcome_is_healthy",
]


def refresh_outcome_is_healthy(refresh: str | None) -> bool:
    return refresh is not None and refresh in _HEALTHY_REFRESH_OUTCOMES


def record_refresh(record: RebaseState, refresh: str | None) -> RebaseState:
    if refresh is None:
        return record
    return record.model_copy(update={"last_refresh": refresh})


def record_when_stale(record: RebaseState, refresh: str | None) -> RebaseState | None:
    if refresh_outcome_is_healthy(refresh):
        return None
    return record_refresh(record, refresh)


def current_branch_or_detached_marker(root: Path) -> str | None:
    from git import Repo
    from git.exc import GitCommandError

    repo: Repo | None = None
    try:
        repo = Repo(root)
        return repo.active_branch.name
    except (TypeError, ValueError, GitCommandError, AttributeError):
        return None
    finally:
        if repo is not None:
            close_method: object = getattr(repo, "close", None)
            if callable(close_method):
                close_method()
