from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from git import GitCommandError
from loguru import logger

from ralph.git import scoped_auto_commit as _scoped
from ralph.git._index_snapshots import _restore_pre_staged_index
from ralph.git._transition_guard import drop_transition_conflicts
from ralph.git.commit_result import CommitCreationStatus
from ralph.git.git_command_queue import route_git_method

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from git import Repo

    from ralph.git.scoped_auto_commit import CreateCommitFn


def _select_deterministic_paths(
    repo: Repo,
    path_list: list[str],
    pre_contents: Mapping[str, str | None],
    intentional_transitions: frozenset[str] | None,
) -> tuple[list[str], list[str]] | _scoped.ScopedCommitResult:
    stageable: list[str] = []
    skipped: list[str] = []
    for path in path_list:
        head_sha = _scoped._read_head_blob_sha(repo, path)
        pre_sha = pre_contents.get(path)
        if head_sha == _scoped._HEAD_PROBE_FAILED:
            logger.warning(
                "commit_deterministic_writes: HEAD metadata probe failed "
                "for {}; refusing to stage the deterministic commit",
                path,
            )
            return _scoped.ScopedCommitResult(
                status=_scoped.ScopedCommitStatus.FAILED,
                skipped_paths=tuple(skipped),
                error=("HEAD metadata probe failed; refusing to stage deterministic commit"),
            )
        if pre_sha is None and head_sha is None:
            if _scoped._has_symlink_ancestor(Path(repo.working_dir) / path):
                ancestor_path = _scoped._symlink_ancestor_path(repo, path)
                if ancestor_path is not None:
                    logger.debug(
                        "commit_deterministic_writes: brand-new path {} "
                        "is beyond a symlink; rewriting to ancestor {} for "
                        "atomic staging",
                        path,
                        ancestor_path,
                    )
                    stageable.append(ancestor_path)
                    continue
            stageable.append(path)
            continue
        if pre_sha is None:
            logger.warning(
                "commit_deterministic_writes: path {} has no pre-write hash "
                "recorded; skipping to avoid sweeping in unrelated changes",
                path,
            )
            skipped.append(path)
            continue
        if head_sha != pre_sha:
            logger.warning(
                "commit_deterministic_writes: path {} was already dirty at HEAD "
                "(head_sha != pre_sha); skipping to keep the chore commit isolated",
                path,
            )
            skipped.append(path)
            continue
        on_disk_sha = _scoped._git_blob_sha(repo, path)
        if on_disk_sha == _scoped._ANCESTOR_SYMLINK_DIRTY:
            ancestor_path = _scoped._symlink_ancestor_path(repo, path)
            if ancestor_path is not None:
                if intentional_transitions is None or ancestor_path not in intentional_transitions:
                    resolved_sha = _scoped._resolved_descendant_sha(repo, path)
                    if resolved_sha is not None and resolved_sha != head_sha:
                        logger.warning(
                            "commit_deterministic_writes: path {} "
                            "resolves through a new symlink ancestor to "
                            "a state that differs from HEAD; the user "
                            "dirtied this descendant after the "
                            "pre-write snapshot, skipping to keep the "
                            "chore commit isolated",
                            path,
                        )
                        skipped.append(path)
                        del on_disk_sha
                        continue
                logger.debug(
                    "commit_deterministic_writes: path {} is beyond a symlink; "
                    "rewriting to ancestor {} for atomic staging",
                    path,
                    ancestor_path,
                )
                stageable.append(ancestor_path)
                del on_disk_sha
                continue
        if on_disk_sha is not None and on_disk_sha == head_sha:
            continue
        stageable.append(path)
        del on_disk_sha

    stageable = sorted(set(stageable))
    if skipped and stageable:
        stageable = drop_transition_conflicts(stageable, skipped)

    return stageable, skipped


def _commit_selected_paths(
    repo: Repo,
    repo_root_path: Path,
    stageable: list[str],
    skipped: list[str],
    pre_staged_snapshots: dict[str, str | None],
    subject: str,
    create_commit_fn: CreateCommitFn,
    stage_fn: Callable[[Path | str, list[str]], None],
    body_builder: Callable[[list[str]], str] | None,
) -> _scoped.ScopedCommitResult:
    if not stageable:
        return _scoped.ScopedCommitResult(
            status=_scoped.ScopedCommitStatus.SKIPPED
            if skipped
            else _scoped.ScopedCommitStatus.NOOP,
            skipped_paths=tuple(skipped),
        )

    try:
        stage_fn(repo_root_path, stageable)
    except (OSError, GitCommandError):
        _ = cast("None", route_git_method(repo, "reset", ("HEAD", "--", *stageable)))
        raise
    try:
        if body_builder is not None:
            body = body_builder(stageable)
        else:
            body_lines = [
                "Auto-generated by Ralph deterministic writer",
                "",
                "Changed files:",
                *(f"- {p}" for p in stageable),
            ]
            body = "\n".join(body_lines)
        message = f"{subject}\n\n{body}"
        expected_head = str(repo.head.commit.hexsha)
        result = create_commit_fn(repo_root_path, message, expected_head=expected_head)
        if result.status is not CommitCreationStatus.CREATED or result.sha is None:
            _ = cast(
                "None",
                route_git_method(repo, "reset", ("HEAD", "--", *stageable)),
            )
            _restore_pre_staged_index(repo, pre_staged_snapshots)
            error_message: str | None
            error_attr: object = getattr(result, "error", None)
            if error_attr is not None:
                error_message = str(error_attr)
            else:
                error_message = "create_commit did not return CREATED"
            return _scoped.ScopedCommitResult(
                status=_scoped.ScopedCommitStatus.FAILED,
                skipped_paths=tuple(skipped),
                error=error_message,
            )
        return _scoped.ScopedCommitResult(
            status=_scoped.ScopedCommitStatus.CREATED,
            sha=result.sha,
            skipped_paths=tuple(skipped),
        )
    except (OSError, GitCommandError) as inner_exc:
        reset_error: str | None = None
        try:
            _ = cast(
                "None",
                route_git_method(repo, "reset", ("HEAD", "--", *stageable)),
            )
        except (OSError, GitCommandError) as reset_exc:
            reset_error = str(reset_exc)
            logger.warning(
                "commit_deterministic_writes: failed-attempt unstage failed; "
                "newly staged paths may remain in the index: {}",
                reset_exc,
            )
        _restore_pre_staged_index(repo, pre_staged_snapshots)
        error_detail = str(inner_exc)
        if reset_error is not None:
            error_detail = f"{error_detail}; rollback reset failed: {reset_error}"
        return _scoped.ScopedCommitResult(
            status=_scoped.ScopedCommitStatus.FAILED,
            skipped_paths=tuple(skipped),
            error=error_detail,
        )
