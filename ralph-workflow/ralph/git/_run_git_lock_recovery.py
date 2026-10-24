"""Lock recovery for every ``run_git`` caller, applied at the single spawn point.

``route_run_git`` is where all of Ralph's git subprocesses are serialised, so a
lock that an orphan left behind is cleared here once instead of at each call
site (rebase, merge, reset, update-ref, push, ...).

Two defences, in order:

* **Pre-flight**: before the command runs, remove locks that are provably dead.
  Commands that write refs get the full sweep (refs, reflogs, worktrees,
  submodules); the rest only check git's few top-level locks, which is a handful
  of ``exists`` calls. This prevents the failure outright, so a command that
  applies part of its work before hitting a lock never has to be re-run.
* **Post-failure**: if git still reports a lock, clear or wait it out. The
  wait happens WITHOUT the queue lock held, so other git work on the repository
  is not frozen behind it. Only commands that are safe to repeat are re-run; a
  stateful command (merge, rebase, ...) has the lock cleared for the caller's
  next attempt and returns its ORIGINAL failure, never a confusing second one.
"""

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

#: Commands that write refs, reflogs or worktree metadata: they get the full pre-flight.
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
#: Commands whose single lock-taking step either happens or does not, so running
#: them again after the lock is cleared is exactly what the first run meant.
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
    """The raw git spawn ``route_run_git`` serialises (``_spawn_git``)."""

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
    """Return the git subcommand in ``args``, skipping global options and their values."""
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
    """Return stderr when it reports a lock problem, else "".

    ``push`` is included on success too: it can publish and still fail to move
    the local tracking ref because of a lock, leaving ``<remote>/<branch>`` stale.
    """
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
    """Run git through ``under_queue`` (which holds the repo's queue lock per call).

    Returns the result, or raises the ``CalledProcessError`` a ``check=True``
    call produced. Always reports the FIRST attempt's failure when recovery does
    not turn it into a success.
    """
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
        # Leave the repository usable for the caller's next attempt, but do not
        # re-run a command that may already have applied part of its work.
        reap_stale_locks(dirs[0], dirs[1], operation=operation)
        return _unwrap(first)

    if not retry_after_lock_failure(dirs[0], dirs[1], text, cwd=cwd, operation=operation):
        return _unwrap(first)
    second = under_queue(lambda: attempt(preflight=False))
    return _unwrap(second if _succeeded(second) else first)
