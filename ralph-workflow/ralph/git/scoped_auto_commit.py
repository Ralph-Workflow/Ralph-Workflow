
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from git import GitCommandError, InvalidGitRepositoryError, NoSuchPathError, Repo
from loguru import logger

from ralph.git import _deterministic_commit_attempt as _deterministic
from ralph.git._dirty_paths import (
    _list_dirty_paths,
    list_dirty_paths,
    path_in_scope,
    snapshot_dirty_paths_strict,
)
from ralph.git._index_snapshots import (
    _GIT_LS_FILES_META_FIELDS,
    _GIT_LS_FILES_PATH_PARTS,
    _capture_staged_paths,
    _restore_pre_staged_index,
    _snapshot_pre_staged_index,
)
from ralph.git.commit_result import CommitCreationResult, CommitCreationStatus
from ralph.git.git_command_queue import route_git_method
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from typing import Protocol

    class CreateCommitFn(Protocol):
        def __call__(
            self, repo_root: Path | str, message: str, *, expected_head: str
        ) -> CommitCreationResult: ...


class ScopedCommitStatus(Enum):

    CREATED = "created"
    NOOP = "noop"
    NOT_REPO = "not_repo"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class ScopedCommitResult:

    status: ScopedCommitStatus
    sha: str | None = None
    skipped_paths: tuple[str, ...] = ()
    error: str | None = None


_HEAD_PROBE_FAILED: str = "__HEAD_PROBE_FAILED__"


def _symlink_ancestor_path(repo: Repo, rel_path: str) -> str | None:
    try:
        working_dir = repo.working_dir
    except (OSError, ValueError):
        return None
    if not working_dir:
        return None
    abs_path = Path(working_dir) / rel_path
    for parent in abs_path.parents:
        try:
            if parent.is_symlink():
                return parent.relative_to(working_dir).as_posix()
        except (OSError, ValueError):
            return None
    return None


def _has_symlink_ancestor(abs_path: Path) -> bool:
    try:
        parents = list(abs_path.parents)
    except OSError:
        return False
    for parent in parents:
        try:
            if parent.is_symlink():
                return True
        except OSError:
            return True
    return False


_ANCESTOR_SYMLINK_DIRTY: str = "__ANCESTOR_SYMLINK_DIRTY__"


def _read_head_blob_sha(repo: Repo, path: str) -> str | None:
    try:
        raw: object = route_git_method(repo, "ls_files", ("--stage", "--", path))
    except GitCommandError:
        return _HEAD_PROBE_FAILED
    if not isinstance(raw, str):
        return _HEAD_PROBE_FAILED
    if not raw.strip():
        return None
    for line in raw.splitlines():
        parts = line.split("\t", 1)
        if len(parts) != _GIT_LS_FILES_PATH_PARTS:
            continue
        meta = parts[0].split()
        if len(meta) < _GIT_LS_FILES_META_FIELDS:
            continue
        if parts[1] == path:
            return meta[1]
    return None


def _file_blob_sha(abs_path: Path) -> str | None:
    try:
        content_bytes = abs_path.read_bytes()
    except OSError:
        return None
    blob = b"blob " + str(len(content_bytes)).encode("ascii") + b"\x00" + content_bytes
    return hashlib.sha1(blob).hexdigest()


def _symlink_blob_sha(abs_path: Path) -> str | None:
    try:
        link_target = abs_path.readlink()
    except OSError:
        return None
    try:
        target_bytes = str(link_target).encode("utf-8")
    except UnicodeEncodeError:
        return None
    blob = b"blob " + str(len(target_bytes)).encode("ascii") + b"\x00" + target_bytes
    return hashlib.sha1(blob).hexdigest()


def _git_blob_sha(repo: Repo, rel_path: str) -> str | None:
    try:
        working_dir = repo.working_dir
    except (OSError, ValueError):
        return None
    if not working_dir:
        return None
    abs_path = Path(working_dir) / rel_path
    if _has_symlink_ancestor(abs_path):
        return _ANCESTOR_SYMLINK_DIRTY
    if abs_path.is_symlink():
        return _symlink_blob_sha(abs_path)
    return _file_blob_sha(abs_path)


def _resolved_descendant_sha(repo: Repo, rel_path: str) -> str | None:
    try:
        working_dir = repo.working_dir
        if not working_dir:
            return None
        resolved = (Path(working_dir) / rel_path).resolve(strict=False)
        if not resolved.is_file():
            return None
        return _file_blob_sha(resolved)
    except (OSError, ValueError):
        return None


def capture_pre_write_contents(
    repo_root: Path | str,
    paths: list[str] | tuple[str, ...],
) -> dict[str, str | None]:
    recorded: dict[str, str | None] = {}
    try:
        repo = Repo(Path(repo_root))
    except (InvalidGitRepositoryError, Exception):
        for path in paths:
            recorded[path] = None
        return recorded
    try:
        for path in paths:
            recorded[path] = _git_blob_sha(repo, path)
    finally:
        close = cast("Callable[[], object] | None", getattr(repo, "close", None))
        if callable(close):
            close()
    return recorded



TargetCommitClass = Literal["tracked", "untracked_ignored", "untracked_new", "not_repo"]




def _classify_untracked_target(repo: Repo, rel_path: str) -> TargetCommitClass | None:
    try:
        route_git_method(repo, "ls_files", ("--error-unmatch", "--", rel_path))
        return "tracked"
    except GitCommandError as exc:
        if exc.status != 1:
            logger.debug(
                "classify_target_for_commit: ls-files failed for {} ({}); treating as not_repo",
                rel_path,
                exc.status,
            )
            return None
    try:
        route_git_method(repo, "check_ignore", ("--quiet", rel_path))
        return "untracked_ignored"
    except GitCommandError as exc:
        if exc.status == 1:
            return "untracked_new"
        logger.debug(
            "classify_target_for_commit: check-ignore failed for {} ({}); treating as not_repo",
            rel_path,
            exc.status,
        )
    return None


def classify_target_for_commit(repo_root: Path | str, path: Path | str) -> TargetCommitClass:
    del repo_root
    target = Path(path).resolve(strict=False)
    repo_anchor = target if DEFAULT_FILE_BACKEND.exists(target) else target.parent
    while not DEFAULT_FILE_BACKEND.exists(repo_anchor):
        repo_anchor = repo_anchor.parent
    try:
        repo = Repo(repo_anchor, search_parent_directories=True)
    except (InvalidGitRepositoryError, NoSuchPathError, OSError, ValueError):
        return "not_repo"
    try:
        working_dir = repo.working_dir
        if not working_dir:
            return "not_repo"
        try:
            rel_path = target.relative_to(Path(working_dir)).as_posix()
        except ValueError:
            return "not_repo"
        untracked_class = _classify_untracked_target(repo, rel_path)
        if untracked_class is not None:
            return untracked_class
        return "not_repo"
    finally:
        close = cast("Callable[[], object] | None", getattr(repo, "close", None))
        if callable(close):
            close()




def commit_deterministic_writes(
    repo_root: Path | str,
    *,
    paths: tuple[str, ...] | list[str],
    pre_contents: Mapping[str, str | None],
    subject: str,
    create_commit_fn: CreateCommitFn,
    stage_fn: Callable[[Path | str, list[str]], None],
    body_builder: Callable[[list[str]], str] | None = None,
    intentional_transitions: frozenset[str] | None = None,
) -> ScopedCommitResult:
    path_list: list[str] = sorted({p for p in paths if p})
    if not path_list:
        return ScopedCommitResult(status=ScopedCommitStatus.NOOP)

    try:
        repo_root_path = Path(repo_root)
        try:
            repo = Repo(repo_root_path)
        except (InvalidGitRepositoryError, Exception):
            logger.debug(
                "commit_deterministic_writes: {} is not a git repo; skipping",
                repo_root_path,
            )
            return ScopedCommitResult(status=ScopedCommitStatus.NOT_REPO)

        try:
            _ = repo.head.commit
        except ValueError as unborn_exc:
            logger.warning(
                "commit_deterministic_writes: {} has an unborn HEAD "
                "(no commits yet); refusing the deterministic commit",
                repo_root_path,
            )
            return ScopedCommitResult(status=ScopedCommitStatus.FAILED, error=str(unborn_exc))

        try:
            pre_staged_paths = _capture_staged_paths(repo)
            pre_staged_snapshots = _snapshot_pre_staged_index(repo, pre_staged_paths)
            if pre_staged_paths:
                _ = cast(
                    "None",
                    route_git_method(repo, "reset", ("HEAD", "--", *pre_staged_paths)),
                )

            try:
                selected = _deterministic._select_deterministic_paths(
                    repo, path_list, pre_contents, intentional_transitions
                )
                if isinstance(selected, ScopedCommitResult):
                    return selected
                stageable, skipped = selected

                return _deterministic._commit_selected_paths(
                    repo,
                    repo_root_path,
                    stageable,
                    skipped,
                    pre_staged_snapshots,
                    subject,
                    create_commit_fn,
                    stage_fn,
                    body_builder,
                )
            finally:
                if pre_staged_snapshots:
                    _restore_pre_staged_index(repo, pre_staged_snapshots)
        finally:
            close = cast("Callable[[], object] | None", getattr(repo, "close", None))
            if callable(close):
                close()
    except (OSError, GitCommandError, ValueError) as exc:
        logger.warning("commit_deterministic_writes: outer guard caught (non-fatal): {}", exc)
        return ScopedCommitResult(status=ScopedCommitStatus.FAILED, error=str(exc))


def _select_scoped_paths(
    repo: Repo,
    scopes: tuple[str, ...],
    exclude: frozenset[str],
    path_filter: Callable[[str], bool] | None,
) -> list[str]:
    all_dirty: list[str] = []
    for scope in sorted(scopes):
        all_dirty.extend(_list_dirty_paths(repo, scope))
    all_dirty = sorted(
        {path for path in all_dirty if path_in_scope(path, scopes) and path not in exclude}
    )
    if path_filter is not None:
        all_dirty = [path for path in all_dirty if path_filter(path)]
    return all_dirty


def _scoped_commit_failure(
    repo: Repo,
    all_dirty: list[str],
    pre_staged_blobs: dict[str, str | None],
    result: CommitCreationResult,
) -> ScopedCommitResult:
    try:
        _ = cast("None", route_git_method(repo, "reset", ("HEAD", "--", *all_dirty)))
    except (OSError, GitCommandError) as reset_exc:
        logger.debug(
            "commit_scoped_updates: failed-attempt unstage failed (non-fatal): {}",
            reset_exc,
        )
    _restore_pre_staged_index(repo, pre_staged_blobs)
    scope_error_attr: object = getattr(result, "error", None)
    if scope_error_attr is not None:
        scope_error_message: str | None = str(scope_error_attr)
    else:
        scope_error_message = "create_commit did not return CREATED"
    return ScopedCommitResult(
        status=ScopedCommitStatus.FAILED,
        error=scope_error_message,
    )




def commit_scoped_updates(
    repo_root: Path | str,
    *,
    scopes: tuple[str, ...],
    subject: str,
    body_builder: Callable[[list[str]], str],
    create_commit_fn: CreateCommitFn,
    stage_fn: Callable[[Path | str, list[str]], None],
    path_filter: Callable[[str], bool] | None = None,
    exclude: frozenset[str] = frozenset(),
) -> ScopedCommitResult:
    try:
        repo_root_path = Path(repo_root)
        try:
            repo = Repo(repo_root_path)
        except (InvalidGitRepositoryError, Exception):
            logger.debug(
                "commit_scoped_updates: {} is not a git repo; skipping auto-commit",
                repo_root_path,
            )
            return ScopedCommitResult(status=ScopedCommitStatus.NOT_REPO)


        try:
            all_dirty = _select_scoped_paths(repo, scopes, exclude, path_filter)
            if not all_dirty:
                return ScopedCommitResult(status=ScopedCommitStatus.NOOP)
            pre_staged_paths = _capture_staged_paths(repo)
            pre_staged_blobs = _snapshot_pre_staged_index(repo, pre_staged_paths)
            if pre_staged_paths:
                _ = cast(
                    "None",
                    route_git_method(repo, "reset", ("HEAD", "--", *pre_staged_paths)),
                )
            try:
                stage_fn(repo_root_path, all_dirty)
                message = f"{subject}\n\n{body_builder(all_dirty)}"
                expected_head = str(repo.head.commit.hexsha)
                result = create_commit_fn(repo_root_path, message, expected_head=expected_head)
                if result.status is not CommitCreationStatus.CREATED or result.sha is None:
                    return _scoped_commit_failure(repo, all_dirty, pre_staged_blobs, result)
                return ScopedCommitResult(status=ScopedCommitStatus.CREATED, sha=result.sha)
            except (OSError, GitCommandError, ValueError):
                try:
                    _ = cast(
                        "None",
                        route_git_method(repo, "reset", ("HEAD", "--", *all_dirty)),
                    )
                except (OSError, GitCommandError, ValueError) as reset_exc:
                    logger.warning(
                        "commit_scoped_updates: failed-attempt unstage failed "
                        "(index may need manual repair): {}",
                        reset_exc,
                    )
                raise
            finally:
                if pre_staged_blobs:
                    try:
                        _restore_pre_staged_index(repo, pre_staged_blobs)
                    except (OSError, GitCommandError) as restore_exc:
                        logger.warning(
                            "commit_scoped_updates: failed to restore pre-staged "
                            "paths (user's staged state may need manual repair): {}",
                            restore_exc,
                        )
        except (OSError, GitCommandError) as exc:
            logger.warning("commit_scoped_updates: auto-commit failed (non-fatal): {}", exc)
            return ScopedCommitResult(status=ScopedCommitStatus.FAILED, error=str(exc))
        finally:
            close = cast("Callable[[], object] | None", getattr(repo, "close", None))
            if callable(close):
                close()
    except (OSError, GitCommandError, ValueError) as exc:
        logger.warning("commit_scoped_updates: outer guard caught (non-fatal): {}", exc)
        return ScopedCommitResult(status=ScopedCommitStatus.FAILED, error=str(exc))


__all__ = [
    "CreateCommitFn",
    "ScopedCommitResult",
    "ScopedCommitStatus",
    "TargetCommitClass",
    "capture_pre_write_contents",
    "commit_deterministic_writes",
    "commit_scoped_updates",
    "list_dirty_paths",
    "path_in_scope",
    "snapshot_dirty_paths_strict",
]
