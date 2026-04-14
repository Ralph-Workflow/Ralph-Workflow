"""Core git rebase helpers (abort/continue/rebase)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence, Protocol

from git import Repo
from git.exc import BadName, GitCommandError, InvalidGitRepositoryError

from .rebase_kinds import RebaseErrorKind, RebaseKind, classify_rebase_error

REBASE_APPLY_DIR = "rebase-apply"
REBASE_MERGE_DIR = "rebase-merge"


@dataclass(frozen=True)
class ProcessResult:
    """Represents the result of running a git subprocess."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0


class ProcessExecutor(Protocol):
    """Executor that runs external processes."""

    def execute(
        self,
        command: str,
        args: Sequence[str],
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
    ) -> ProcessResult:
        ...


@dataclass(frozen=True)
class SubprocessExecutor:
    """Default executor powered by subprocess."""

    def execute(
        self,
        command: str,
        args: Sequence[str],
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
    ) -> ProcessResult:
        cmd = (command, *args)
        process = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        return ProcessResult(
            returncode=process.returncode,
            stdout=process.stdout.strip(),
            stderr=process.stderr.strip(),
        )


class RebaseOperationError(Exception):
    """Raised when a rebase operation fails."""


@dataclass(frozen=True)
class RebaseSuccess:
    """Rebase completed successfully."""


@dataclass(frozen=True)
class RebaseConflicts:
    """Rebase stopped because conflicts remain."""

    files: list[str]


@dataclass(frozen=True)
class RebaseNoOp:
    """Rebase was not applicable (already up-to-date or invalid state)."""

    reason: str


@dataclass(frozen=True)
class RebaseFailed:
    """Rebase failed with a specific error kind."""

    kind: RebaseErrorKind


RebaseResult = RebaseSuccess | RebaseConflicts | RebaseNoOp | RebaseFailed


def abort_rebase(
    *,
    repo_root: Path | str | None = None,
    executor: ProcessExecutor | None = None,
) -> None:
    """Abort an in-progress rebase."""

    path = _resolve_repo_root(repo_root)
    executor = executor or SubprocessExecutor()

    if not rebase_in_progress(path):
        raise RebaseOperationError("No rebase in progress")

    result = executor.execute("git", ("rebase", "--abort"), cwd=path)
    if not result.succeeded:
        raise RebaseOperationError(
            f"Failed to abort rebase: {result.stderr or result.stdout or 'unknown error'}"
        )


def continue_rebase(
    *,
    repo_root: Path | str | None = None,
    executor: ProcessExecutor | None = None,
) -> None:
    """Continue an in-progress rebase after conflicts have been resolved."""

    path = _resolve_repo_root(repo_root)
    executor = executor or SubprocessExecutor()

    if not rebase_in_progress(path):
        raise RebaseOperationError("No rebase in progress")

    conflicts = get_conflicted_files(repo_root=path, executor=executor)
    if conflicts:
        raise RebaseOperationError(
            f"Conflicts remain: {len(conflicts)} file(s) still have conflicts"
        )

    result = executor.execute("git", ("rebase", "--continue"), cwd=path)
    if not result.succeeded:
        raise RebaseOperationError(
            f"Failed to continue rebase: {result.stderr or result.stdout or 'unknown error'}"
        )


def rebase_in_progress(repo_root: Path | str | None = None) -> bool:
    """Return True when a rebase directory exists in the git repo."""

    path = _resolve_repo_root(repo_root)
    git_dir = _git_dir(path)
    return any((git_dir / marker).exists() for marker in (REBASE_APPLY_DIR, REBASE_MERGE_DIR))


def get_conflicted_files(
    *,
    repo_root: Path | str | None = None,
    executor: ProcessExecutor | None = None,
) -> list[str]:
    """List files that are currently marked as conflicted in the index."""

    path = _resolve_repo_root(repo_root)
    executor = executor or SubprocessExecutor()

    result = executor.execute(
        "git",
        ("status", "--porcelain", "--untracked-files=no"),
        cwd=path,
    )

    if result.returncode != 0:
        raise RebaseOperationError(
            f"Failed to list conflicted files: {result.stderr or result.stdout or 'unknown'}"
        )

    conflicts: list[str] = []
    for line in result.stdout.splitlines():
        if not line:
            continue

        prefix = line[:2]
        if "U" not in prefix:
            continue

        payload = line[3:] if len(line) > 3 else ""
        filename = payload.split(" -> ")[-1].strip()
        if filename:
            conflicts.append(filename)

    return sorted(dict.fromkeys(conflicts))


def rebase_onto(
    upstream_branch: str,
    *,
    repo_root: Path | str | None = None,
    executor: ProcessExecutor | None = None,
) -> RebaseResult:
    """Rebase the current branch on top of the provided upstream branch."""

    path = _resolve_repo_root(repo_root)
    executor = executor or SubprocessExecutor()

    repo = _open_repo(path)
    head_commit = _safe_head_commit(repo)
    if head_commit is None:
        return RebaseNoOp("Repository has no commits yet (unborn branch)")

    try:
        repo.commit(upstream_branch)
    except (BadName, GitCommandError):
        return RebaseFailed(
            RebaseErrorKind(kind=RebaseKind.INVALID_REVISION, metadata={"revision": upstream_branch})
        )

    branch_name = _active_branch_name(repo)
    if branch_name is None:
        return RebaseNoOp("HEAD is detached (not on any branch), rebase not applicable")

    if branch_name in {"main", "master"}:
        return RebaseNoOp(f"Already on '{branch_name}' branch, rebase not applicable")

    if _merge_base_is_ancestor(executor, path, upstream_branch):
        return RebaseNoOp("Branch is already up-to-date with upstream")

    result = executor.execute("git", ("rebase", upstream_branch), cwd=path)
    if result.succeeded:
        return RebaseSuccess()

    if _contains_up_to_date_message(result):
        return RebaseNoOp("Branch is already up-to-date with upstream")

    error_kind = classify_rebase_error(result.stderr, result.stdout)
    if error_kind.kind == RebaseKind.CONTENT_CONFLICT:
        files = get_conflicted_files(repo_root=path)
        return RebaseConflicts(files)

    return RebaseFailed(error_kind)


def _resolve_repo_root(repo_root: Path | str | None = None) -> Path:
    candidate = Path(repo_root) if repo_root else Path.cwd()
    try:
        repo = Repo(candidate, search_parent_directories=True)
    except InvalidGitRepositoryError as exc:
        raise RebaseOperationError(f"Not a git repository: {exc}") from exc

    if not repo.working_tree_dir:
        raise RebaseOperationError("Cannot determine git working tree directory")

    return Path(repo.working_tree_dir).resolve()


def _open_repo(repo_root: Path) -> Repo:
    try:
        return Repo(repo_root)
    except InvalidGitRepositoryError as exc:
        raise RebaseOperationError(f"Not a git repository: {exc}") from exc


def _git_dir(repo_root: Path) -> Path:
    repo = _open_repo(repo_root)
    git_dir = repo.git_dir
    if not git_dir:
        raise RebaseOperationError("Cannot determine .git directory for repository")
    return Path(git_dir).resolve()


def _safe_head_commit(repo: Repo):
    try:
        return repo.head.commit
    except (ValueError, GitCommandError, AttributeError):
        return None


def _active_branch_name(repo: Repo) -> str | None:
    try:
        return repo.active_branch.name
    except (TypeError, ValueError, GitCommandError):
        return None


def _merge_base_is_ancestor(executor: ProcessExecutor, repo_root: Path, upstream_branch: str) -> bool:
    result = executor.execute(
        "git",
        ("merge-base", "--is-ancestor", upstream_branch, "HEAD"),
        cwd=repo_root,
    )
    return result.returncode == 0


def _contains_up_to_date_message(result: ProcessResult) -> bool:
    payload = f"{result.stderr}\n{result.stdout}".lower()
    return "up to date" in payload or "up-to-date" in payload


__all__ = [
    "ProcessExecutor",
    "ProcessResult",
    "SubprocessExecutor",
    "RebaseOperationError",
    "RebaseResult",
    "RebaseSuccess",
    "RebaseConflicts",
    "RebaseNoOp",
    "RebaseFailed",
    "abort_rebase",
    "continue_rebase",
    "get_conflicted_files",
    "rebase_in_progress",
    "rebase_onto",
]
