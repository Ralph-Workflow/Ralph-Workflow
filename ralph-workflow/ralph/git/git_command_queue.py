
from __future__ import annotations

import importlib
import os
import sys
import threading
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, TypeGuard

from git import Repo
from git.exc import GitCommandError

from ralph.git._git_command_queue_lease import (
    _acquire_lease,
    _lease_is_held,
    _lease_lock_path,
    _WorktreeLease,
    _WorktreeLeaseHolder,
    _WorktreeLeaseUnavailableError,
)
from ralph.git._lock_recovery import (
    git_dirs_from_git_dir,
    is_lock_failure,
    reap_known_stale_locks,
    reap_stale_locks,
    retry_after_lock_failure,
)
from ralph.git._run_git_lock_recovery import (
    is_ref_writing,
    is_safe_to_repeat,
    run_with_lock_recovery,
)
from ralph.git._worktree_lease import (
    LeaseClock,
    LeaseLiveness,
    LeaseStealDecision,
    LeaseStealReason,
    decide_lease_steal,
    default_lease_clock,
    probe_lease_holder_liveness,
)
from ralph.git.git_call_policy import (
    GitCallClass,
    classify_git_exception,
    classify_git_outcome,
    effective_deadline_seconds,
)
from ralph.git.git_run_result import (
    GitOutcome,
    GitOutcomeKind,
    GitRunResult,
    succeeded_outcome,
    timed_out_outcome,
    unknown_outcome,
)
from ralph.git.subprocess_runner import GitRunOptions

if TYPE_CHECKING:
    import os
    import subprocess
    from collections.abc import Callable, Mapping, Sequence


class RepoPath(Protocol):

    @property
    def git_dir(self) -> str | os.PathLike[str]: ...


class GitMethodRepo(RepoPath, Protocol):

    @property
    def git(self) -> object: ...

    @property
    def working_dir(self) -> str | os.PathLike[str] | None: ...


class _SpawnGit(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None,
        label: str,
        options: GitRunOptions | None = None,
    ) -> GitRunResult: ...


class _SpawnModule(Protocol):
    _spawn_git: object


def _is_git_method(value: object) -> TypeGuard[Callable[..., object]]:
    return callable(value)


def _is_spawn_git(value: object) -> TypeGuard[_SpawnGit]:
    return callable(value)


def _is_spawn_module(value: object) -> TypeGuard[_SpawnModule]:
    return hasattr(value, "_spawn_git")



_HEALTH_LOG_CAP: int = 256

_MAX_CONSECUTIVE_TIMEOUTS: int = 64

_LEASE_STALE_SECONDS: float = 600.0


@dataclass(frozen=True)
class QueuedCallOutcome:

    kind: Literal["success", "timeout", "error"]
    outcome: GitOutcome = field(default_factory=lambda: GitOutcome(GitOutcomeKind.UNKNOWN))


class GitCommandQueue:

    common_dir: Path
    execute_lock: threading.RLock
    consecutive_timeouts: int
    outcome_log: deque[QueuedCallOutcome]

    def __init__(self, common_dir: Path) -> None:
        self.common_dir = common_dir
        self.execute_lock = threading.RLock()
        self.consecutive_timeouts = 0
        self.outcome_log = deque(
            maxlen=_HEALTH_LOG_CAP
        )

    def record_outcome(
        self,
        kind: Literal["success", "timeout", "error"],
        *,
        outcome: GitOutcome | None = None,
    ) -> None:
        if kind == "timeout":
            self.consecutive_timeouts = min(
                self.consecutive_timeouts + 1, _MAX_CONSECUTIVE_TIMEOUTS
            )
        else:
            self.consecutive_timeouts = 0
        self.outcome_log.append(
            QueuedCallOutcome(kind=kind, outcome=outcome or _default_outcome_for(kind))
        )

    def health_factor(self) -> int:
        return self.consecutive_timeouts

    def reset_health(self) -> None:
        self.consecutive_timeouts = 0
        self.outcome_log.clear()


if not (_HEALTH_LOG_CAP > 0):
    raise RuntimeError(f"_HEALTH_LOG_CAP must be positive; got {_HEALTH_LOG_CAP!r}")
if not (_MAX_CONSECUTIVE_TIMEOUTS > 0):
    raise RuntimeError(
        f"_MAX_CONSECUTIVE_TIMEOUTS must be positive; got {_MAX_CONSECUTIVE_TIMEOUTS!r}"
    )
if not (_LEASE_STALE_SECONDS > 0):
    raise RuntimeError(f"_LEASE_STALE_SECONDS must be positive; got {_LEASE_STALE_SECONDS!r}")




@dataclass
class _QueueRegistry:

    _queues: dict[str, GitCommandQueue] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def get_or_create(self, common_dir: Path) -> GitCommandQueue:
        key = str(common_dir)
        existing: GitCommandQueue | None
        with self._lock:
            existing = self._queues.get(key)
            if existing is not None:
                return existing
            created = GitCommandQueue(common_dir)
            self._queues[key] = created
            return created

    def reset_all(self) -> None:
        with self._lock:
            for queue in self._queues.values():
                queue.reset_health()
            self._queues.clear()


_REGISTRY: _QueueRegistry = _QueueRegistry()


def _resolve_common_dir(repo_or_path: RepoPath | Path | str) -> Path:
    if not isinstance(repo_or_path, (Path, str)):
        return _normalize_git_dir(Path(repo_or_path.git_dir))
    path = Path(repo_or_path).expanduser()
    return _normalize_git_dir(path)


_WORKTREE_GIT_DIR_MIN_PARTS: int = 3

_WORKTREE_GIT_DIR_UP_LEVELS: int = 2


def _normalize_git_dir(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=False)
    except ValueError as exc:
        raise OSError("embedded null byte in path") from exc
    git_marker = resolved / ".git"
    if git_marker.is_dir():
        resolved = git_marker
    elif git_marker.is_file():
        try:
            first_line = git_marker.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0]
        except OSError:
            first_line = ""
        if first_line.startswith("gitdir:"):
            pointed = Path(first_line[len("gitdir:") :].strip())
            if not pointed.is_absolute():
                pointed = resolved / pointed
            resolved = pointed.resolve(strict=False)
    parts = resolved.parts
    if len(parts) >= _WORKTREE_GIT_DIR_MIN_PARTS and parts[-2] == "worktrees":
        common = resolved
        for _ in range(_WORKTREE_GIT_DIR_UP_LEVELS):
            common = common.parent
        return common.resolve(strict=False)
    return resolved


def get_queue_for_repo(repo_or_path: RepoPath | Path | str) -> GitCommandQueue:
    common = _resolve_common_dir(repo_or_path)
    return _REGISTRY.get_or_create(common)


def reset_all_queues() -> None:
    _REGISTRY.reset_all()




def route_git_method(
    repo: GitMethodRepo,
    method_name: str,
    args: Sequence[object] = (),
    kwargs: Mapping[str, object] | None = None,
) -> object:
    queue = get_queue_for_repo(repo)
    call_kwargs: Mapping[str, object] = kwargs if kwargs is not None else {}
    subcommand = method_name.replace("_", "-")
    dirs = git_dirs_from_git_dir(Path(str(repo.git_dir))) if hasattr(repo, "git_dir") else None
    operation = f"repo.git.{method_name}"

    def attempt(*, preflight: bool) -> tuple[object, GitCommandError | None]:
        with queue.execute_lock:
            if preflight and dirs is not None:
                if is_ref_writing(subcommand):
                    reap_stale_locks(dirs[0], dirs[1], operation=operation)
                else:
                    reap_known_stale_locks(dirs[0], dirs[1], operation=operation)
            return _dispatch_git_method(queue, repo, method_name, args, call_kwargs)

    first_result, first_error = attempt(preflight=True)
    if first_error is None:
        return first_result
    text = str(first_error)
    if dirs is None or not is_lock_failure(text):
        raise first_error
    if not is_safe_to_repeat(subcommand):
        reap_stale_locks(dirs[0], dirs[1], operation=operation)
        raise first_error
    working_dir = repo.working_dir if hasattr(repo, "working_dir") else None
    cwd = Path(str(working_dir)) if working_dir else dirs[0]
    if not retry_after_lock_failure(dirs[0], dirs[1], text, cwd=cwd, operation=operation):
        raise first_error
    second_result, second_error = attempt(preflight=False)
    if second_error is not None:
        raise first_error
    return second_result


def _dispatch_git_method(
    queue: GitCommandQueue,
    repo: GitMethodRepo,
    method_name: str,
    args: Sequence[object],
    call_kwargs: Mapping[str, object],
) -> tuple[object, GitCommandError | None]:
    git_attr = repo.git
    if git_attr is None:
        queue.record_outcome("error")
        msg = f"repo {repo!r} has no .git attribute"
        raise AttributeError(msg)
    try:
        method: object = getattr(git_attr, method_name)
    except AttributeError as exc:
        queue.record_outcome("error")
        raise AttributeError(f"repo.git has no method named {method_name!r}") from exc
    try:
        if not _is_git_method(method):
            raise TypeError(f"repo.git attribute {method_name!r} is not callable")
        result = method(*args, **call_kwargs)
    except GitCommandError as exc:
        queue.record_outcome(_classify_exception(exc), outcome=classify_git_exception(exc))
        return None, exc
    except BaseException as exc:
        queue.record_outcome(_classify_exception(exc), outcome=classify_git_exception(exc))
        raise
    queue.record_outcome("success", outcome=succeeded_outcome())
    return result, None


def _default_outcome_for(kind: Literal["success", "timeout", "error"]) -> GitOutcome:
    if kind == "success":
        return succeeded_outcome()
    if kind == "timeout":
        return timed_out_outcome()
    return unknown_outcome("unclassified_error")


def _classify_exception(exc: BaseException) -> Literal["timeout", "error"]:
    if isinstance(exc, TimeoutError):
        return "timeout"
    if type(exc).__qualname__ == "TimeoutExpired":
        return "timeout"
    return "error"




def route_run_git(
    args: Sequence[str],
    *,
    cwd: Path | None,
    label: str,
    options: GitRunOptions | None = None,
    call_class: GitCallClass | None = None,
) -> GitRunResult:
    spawn_module: object = sys.modules.get("ralph.git.subprocess_runner")
    if spawn_module is None:
        spawn_module = importlib.import_module("ralph.git.subprocess_runner")
    if not _is_spawn_module(spawn_module):
        raise TypeError("ralph.git.subprocess_runner has no _spawn_git")
    spawn_git: object = spawn_module._spawn_git
    if not _is_spawn_git(spawn_git):
        raise TypeError("ralph.git.subprocess_runner._spawn_git is not callable")

    effective_options = _apply_call_class_deadline(options, call_class)
    queue = get_queue_for_repo(cwd if cwd is not None else Path.cwd())

    def under_queue(
        call: Callable[
            [],
            tuple[GitRunResult | None, subprocess.CalledProcessError | None],
        ],
    ) -> tuple[GitRunResult | None, subprocess.CalledProcessError | None]:
        with queue.execute_lock:
            try:
                attempt = call()
            except BaseException as exc:
                queue.record_outcome(_classify_exception(exc), outcome=classify_git_exception(exc))
                raise
            result, error = attempt
            if error is not None:
                queue.record_outcome("error", outcome=classify_git_exception(error))
            elif result is None:
                queue.record_outcome("error", outcome=unknown_outcome("no_result"))
            else:
                outcome = classify_git_outcome(returncode=result.returncode)
                queue_kind: Literal["success", "error"] = (
                    "success" if outcome.kind is GitOutcomeKind.SUCCEEDED else "error"
                )
                queue.record_outcome(queue_kind, outcome=outcome)
            return attempt

    result = run_with_lock_recovery(under_queue, spawn_git, args, cwd, label, effective_options)
    return replace(result, outcome=classify_git_outcome(returncode=result.returncode))


def _apply_call_class_deadline(
    options: GitRunOptions | None,
    call_class: GitCallClass | None,
) -> GitRunOptions | None:
    effective_class = call_class
    if effective_class is None and options is not None:
        effective_class = options.call_class
    if effective_class is None:
        return options
    deadline = effective_deadline_seconds(effective_class)
    if options is None:
        return GitRunOptions(timeout=deadline, call_class=effective_class)
    effective_timeout = options.timeout if options.timeout is not None else deadline
    return replace(
        options,
        timeout=effective_timeout,
        call_class=effective_class,
    )



WorktreeLeaseHolder = _WorktreeLeaseHolder
WorktreeLeaseUnavailableError = _WorktreeLeaseUnavailableError
WorktreeLease = _WorktreeLease

WorktreeLeaseUnavailable = _WorktreeLeaseUnavailableError


_lease_holder_liveness_probe: Callable[[Path, int | None], LeaseLiveness] = (
    probe_lease_holder_liveness
)

_lease_clock: LeaseClock = default_lease_clock()


def begin_agent_lease(
    repo_or_path: RepoPath | Path | str,
    *,
    holder: WorktreeLeaseHolder | None = None,
) -> WorktreeLease:
    repo = _repo_or_open(repo_or_path)
    lock_path = _lease_lock_path(_resolve_common_dir(repo))
    effective_holder = holder or WorktreeLeaseHolder(name="agent-phase")
    return _acquire_lease(
        lock_path,
        effective_holder,
        stale_seconds=_LEASE_STALE_SECONDS,
        probe=_lease_holder_liveness_probe,
        clock=_lease_clock,
    )


def try_acquire_lease(
    repo_or_path: RepoPath | Path | str,
    *,
    holder: WorktreeLeaseHolder | None = None,
) -> WorktreeLease | None:
    try:
        return begin_agent_lease(repo_or_path, holder=holder)
    except WorktreeLeaseUnavailable:
        return None


def end_agent_lease(lease: WorktreeLease) -> None:
    lease.release()


def is_lease_held(repo_or_path: RepoPath | Path | str) -> bool:
    repo = _repo_or_open(repo_or_path)
    lock_path = _lease_lock_path(_resolve_common_dir(repo))
    return _lease_is_held(
        lock_path,
        probe=_lease_holder_liveness_probe,
        clock=_lease_clock,
    )


def _repo_or_open(repo_or_path: RepoPath | Path | str) -> RepoPath:
    if isinstance(repo_or_path, (Path, str)):
        return Repo(Path(repo_or_path))
    return repo_or_path




__all__ = [
    "GitCommandQueue",
    "LeaseLiveness",
    "LeaseStealDecision",
    "LeaseStealReason",
    "QueuedCallOutcome",
    "WorktreeLease",
    "WorktreeLeaseHolder",
    "WorktreeLeaseUnavailable",
    "WorktreeLeaseUnavailableError",
    "begin_agent_lease",
    "decide_lease_steal",
    "end_agent_lease",
    "get_queue_for_repo",
    "is_lease_held",
    "reset_all_queues",
    "route_git_method",
    "route_run_git",
    "try_acquire_lease",
]
