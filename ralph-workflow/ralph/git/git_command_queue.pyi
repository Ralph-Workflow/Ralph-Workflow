from __future__ import annotations

import os
import threading
from collections import deque
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal, Protocol

from git import Repo

from ralph.git.git_run_result import GitRunResult
from ralph.git.subprocess_runner import GitRunOptions

#: Capacity of the bounded outcome log retained on each queue
_HEALTH_LOG_CAP: int

#: Saturation ceiling for consecutive-timeout counter
_MAX_CONSECUTIVE_TIMEOUTS: int

#: Minimum path depth for a recognized linked-worktree git dir
_WORKTREE_GIT_DIR_MIN_PARTS: int

#: Trailing path parts in a worktree git dir above the common git dir
_WORKTREE_GIT_DIR_UP_LEVELS: int

#: Minimum ``|``-split fields in a parsed lease lock-file line
_LEASE_LINE_MIN_FIELDS: int

class QueuedCallOutcome:
    kind: Literal["success", "timeout", "error"]

class GitCommandQueue:
    common_dir: Path
    execute_lock: threading.RLock
    consecutive_timeouts: int
    outcome_log: deque[QueuedCallOutcome]
    def __init__(self, common_dir: Path) -> None: ...
    def record_outcome(self, kind: Literal["success", "timeout", "error"]) -> None: ...
    def health_factor(self) -> int: ...
    def reset_health(self) -> None: ...

class WorktreeLeaseHolder:
    name: str
    acquired_at_unix: float
    def __init__(self, name: str, acquired_at_unix: float = ...) -> None: ...

class WorktreeLeaseUnavailableError(RuntimeError): ...

class RepoPath(Protocol):
    @property
    def git_dir(self) -> str | os.PathLike[str]: ...

class GitMethodRepo(RepoPath, Protocol):
    @property
    def git(self) -> object: ...

WorktreeLeaseUnavailable: type = WorktreeLeaseUnavailableError

class WorktreeLease:
    lock_path: Path
    holder: WorktreeLeaseHolder
    def release(self) -> None: ...
    def __enter__(self) -> WorktreeLease: ...
    def __exit__(self, exc_type: object, exc: object, tb: object) -> None: ...

def route_git_method(
    repo: GitMethodRepo,
    method_name: str,
    args: Sequence[object] = ...,
    kwargs: Mapping[str, object] | None = ...,
) -> object: ...
def route_run_git(
    args: Sequence[str],
    *,
    cwd: Path | None,
    label: str,
    options: GitRunOptions | None = ...,
) -> GitRunResult: ...
def get_queue_for_repo(repo_or_path: RepoPath | Path | str) -> GitCommandQueue: ...
def reset_all_queues() -> None: ...
def begin_agent_lease(
    repo_or_path: Repo | Path | str,
    *,
    holder: WorktreeLeaseHolder | None = ...,
) -> WorktreeLease: ...
def try_acquire_lease(
    repo_or_path: Repo | Path | str,
    *,
    holder: WorktreeLeaseHolder | None = ...,
) -> WorktreeLease | None: ...
def end_agent_lease(lease: WorktreeLease) -> None: ...
def is_lease_held(repo_or_path: Repo | Path | str) -> bool: ...
def _classify_exception(exc: BaseException) -> Literal["timeout", "error"]: ...
