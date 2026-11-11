
from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Protocol

from ralph.git._lock_recovery import (
    find_git_dirs,
    is_lock_failure,
    reap_known_stale_locks,
    reap_stale_locks,
    retry_after_lock_failure,
)
from ralph.git.git_run_result import GitRunResult

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from ralph.git.subprocess_runner import GitRunOptions

_REF_WRITING = frozenset(
    {
        "merge",
        "rebase",
        "cherry-pick",
        "am",
        "stash",
        "revert",
        "pull",
        "checkout",
        "switch",
        "branch",
        "update-ref",
        "tag",
        "push",
        "fetch",
        "commit",
        "reset",
        "worktree",
        "pack-refs",
        "gc",
        "symbolic-ref",
    }
)
_SAFE_TO_REPEAT = frozenset(
    {
        "add",
        "commit",
        "update-ref",
        "update-index",
        "fetch",
        "branch",
        "config",
        "reset",
        "tag",
        "rm",
        "pack-refs",
        "symbolic-ref",
    }
)
_OPTIONS_WITH_VALUE = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})

_Attempt = tuple["GitRunResult | None", "subprocess.CalledProcessError | None"]


class SpawnGit(Protocol):

    def __call__(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None,
        label: str,
        options: GitRunOptions | None = None,
    ) -> GitRunResult: ...


def is_ref_writing(subcommand: str | None) -> bool:
    return subcommand in _REF_WRITING


def is_safe_to_repeat(subcommand: str | None) -> bool:
    return subcommand in _SAFE_TO_REPEAT


def git_subcommand(args: Sequence[str]) -> str | None:
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg in _OPTIONS_WITH_VALUE:
            skip = True
        elif not arg.startswith("-"):
            return arg
    return None


def _failure_text(subcommand: str | None, attempt: _Attempt) -> str:
    result, error = attempt
    if error is not None:
        raw: object = error.stderr
        text = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw or "")
    elif result is not None and (result.returncode != 0 or subcommand == "push"):
        text = result.stderr
    else:
        return ""
    return text if is_lock_failure(text) else ""


def _unwrap(attempt: _Attempt) -> GitRunResult:
    result, error = attempt
    if error is not None:
        raise error
    if not isinstance(result, GitRunResult):
        raise TypeError("_spawn_git returned a non-GitRunResult value")
    return result


def _succeeded(attempt: _Attempt) -> bool:
    result, error = attempt
    return error is None and isinstance(result, GitRunResult) and result.returncode == 0


def run_with_lock_recovery(
    under_queue: Callable[[Callable[[], _Attempt]], _Attempt],
    spawn_git: SpawnGit,
    args: Sequence[str],
    cwd: Path | None,
    label: str,
    options: GitRunOptions | None,
) -> GitRunResult:
    dirs = find_git_dirs(cwd) if cwd is not None else None
    subcommand = git_subcommand(args)
    operation = f"run_git:{label}"

    def attempt(*, preflight: bool) -> _Attempt:
        if preflight and dirs is not None:
            if subcommand in _REF_WRITING:
                reap_stale_locks(dirs[0], dirs[1], operation=operation)
            else:
                reap_known_stale_locks(dirs[0], dirs[1], operation=operation)
        try:
            return spawn_git(args, cwd=cwd, label=label, options=options), None
        except subprocess.CalledProcessError as exc:
            return None, exc

    first = under_queue(lambda: attempt(preflight=True))
    text = _failure_text(subcommand, first)
    if not text or dirs is None or cwd is None:
        return _unwrap(first)

    if subcommand not in _SAFE_TO_REPEAT:
        reap_stale_locks(dirs[0], dirs[1], operation=operation)
        return _unwrap(first)

    if not retry_after_lock_failure(dirs[0], dirs[1], text, cwd=cwd, operation=operation):
        return _unwrap(first)
    second = under_queue(lambda: attempt(preflight=False))
    return _unwrap(second if _succeeded(second) else first)
