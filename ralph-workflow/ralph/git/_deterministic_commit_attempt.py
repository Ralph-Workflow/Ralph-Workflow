"""Selection and rollback for isolated deterministic commit attempts."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from git import GitCommandError
from loguru import logger

from ralph.git import scoped_auto_commit as _scoped
from ralph.git._index_snapshots import _restore_pre_staged_index
from ralph.git._transition_guard import drop_transition_conflicts
from ralph.git.commit_result import CommitCreationStatus

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
    """Select writer-owned paths without staging or changing the index."""
    stageable: list[str] = []
    skipped: list[str] = []
    for path in path_list:
        head_sha = _scoped._read_head_blob_sha(repo, path)
        pre_sha = pre_contents.get(path)
        if head_sha == _scoped._HEAD_PROBE_FAILED:
            # HEAD metadata probe failed -- cannot distinguish
            # "absent at HEAD" from a broken index. Fail closed:
            # stage NOTHING and report FAILED (wt-012 DA-006/
            # DA-011). The finally below restores the pre-staged
            # snapshot, so the index stays byte-for-byte intact.
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
            # Brand-new path: absent on disk at pre-write capture
            # AND confirmed absent at HEAD -- the deterministic
            # writer authored the whole file, so committing it
            # cannot sweep in anyone else's work (wt-012 DA-006).
            # wt-012 DA-007/DA-012: a brand-new path that sits
            # under a newly-installed symlink ancestor (e.g. a
            # ``shutil.copytree`` fallback that materialized a
            # leaf under a sibling root now backed by a
            # symlink) cannot be staged as its lexical path --
            # ``git add --all`` would fatal with ``pathspec ...
            # is beyond a symbolic link``. Rewrite the
            # brand-new path to the symlink ancestor so the
            # new symlink and the materialized leaves land
            # atomically in one commit.
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
            # Caller did not record a pre-write hash for this
            # path (or the file existed at HEAD but was missing
            # on disk at capture time). Treat as suspicious --
            # the deterministic writer should know what every
            # path's prior content was. SKIP rather than commit
            # a path we cannot isolate.
            logger.warning(
                "commit_deterministic_writes: path {} has no pre-write hash "
                "recorded; skipping to avoid sweeping in unrelated changes",
                path,
            )
            skipped.append(path)
            continue
        if head_sha != pre_sha:
            # HEAD does not match the caller's pre-write hash:
            # the user or agent dirtied the file in between
            # the writer's snapshot and the commit attempt.
            # SKIP so the chore commit cannot ride their work.
            logger.warning(
                "commit_deterministic_writes: path {} was already dirty at HEAD "
                "(head_sha != pre_sha); skipping to keep the chore commit isolated",
                path,
            )
            skipped.append(path)
            continue
        # HEAD matches the pre-write hash, so the on-disk
        # content the writer just produced is the only diff
        # for this path since HEAD. Confirm the on-disk
        # content also differs from HEAD (otherwise nothing
        # to commit). wt-012 PA-002: an ancestor-symlink
        # shape (a deleted sibling dir replaced by a
        # symlink that resolves to a same-named same-content
        # file) makes ``git hash-object`` return a value
        # that the cheap ``== head_sha`` comparison cannot
        # distinguish from "no real change" -- the
        # ``_scoped._ANCESTOR_SYMLINK_DIRTY`` sentinel forces the
        # path into ``stageable`` instead.
        on_disk_sha = _scoped._git_blob_sha(repo, path)
        if on_disk_sha == _scoped._ANCESTOR_SYMLINK_DIRTY:
            # The path is lexically unreachable from HEAD
            # (an ancestor is a symlink to a target that no
            # longer contains this file). Replacing the
            # descendant with its nearest symlink ancestor
            # in the stageable set lets ``git add --all``
            # pick up both the new symlink and the
            # descendant deletions atomically -- without
            # this rewrite ``git add`` would fatal with
            # ``pathspec ... is beyond a symbolic link``.
            ancestor_path = _scoped._symlink_ancestor_path(repo, path)
            if ancestor_path is not None:
                # wt-012 DA-003/DA-010/DA-012: a user change
                # that lands BETWEEN the producer's pre-write
                # capture and the install makes the recorded
                # ``pre_sha`` stale, and the cheap
                # HEAD-vs-pre-sha equality check above does
                # not catch it. When the resolved on-disk
                # state (the file the symlink target now
                # exposes) also differs from ``head_sha``,
                # the user must have dirtied the descendant
                # AFTER the capture, and committing the
                # ancestor transition would sweep the user's
                # deletion into the chore commit. Mark the
                # descendant as SKIPPED so
                # ``drop_transition_conflicts`` drops the
                # ancestor too. The caller can opt out via
                # ``intentional_transitions`` when the
                # transition's resolved content is the
                # install's own canonical target.
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
                del on_disk_sha  # narrow explicit type for the next iter
                continue
        if on_disk_sha is not None and on_disk_sha == head_sha:
            # No actual change since HEAD -- skip.
            continue
        stageable.append(path)
        del on_disk_sha  # narrow explicit type for the next iter

    # Dedupe (a dir→symlink rewrite can append the same
    # ancestor once per descendant plus once for the root).
    # wt-012 DA-003/DA-008: drop ancestors whose stage would
    # sweep a SKIPPED dirty descendant's deletion.
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
    """Commit selected paths, rolling back this attempt on any commit failure."""
    if not stageable:
        return _scoped.ScopedCommitResult(
            status=_scoped.ScopedCommitStatus.SKIPPED
            if skipped
            else _scoped.ScopedCommitStatus.NOOP,
            skipped_paths=tuple(skipped),
        )

    # Stage EXACTLY the stageable set via the injected
    # stage_fn so the production git plumbing is the same
    # code path the rest of the pipeline uses. wt-012 DA-006:
    # a partial staging failure must NOT leave the newly
    # staged set half-applied, so the stage call is inside
    # the same try that owns the failed-attempt rollback.
    try:
        stage_fn(repo_root_path, stageable)
    except (OSError, GitCommandError):
        _ = cast("None", repo.git.reset("HEAD", "--", *stageable))
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
            # Failed commit -- rollback the stage we just did
            # and restore the pre-staged snapshot. NO half
            # staged index.
            _ = cast(
                "None",
                repo.git.reset("HEAD", "--", *stageable),
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
        # Commit raised -- rollback the stage and restore the
        # pre-staged snapshot so the failed attempt leaves no
        # half-staged debris.
        reset_error: str | None = None
        try:
            _ = cast(
                "None",
                repo.git.reset("HEAD", "--", *stageable),
            )
        except (OSError, GitCommandError) as reset_exc:  # pragma: no cover
            # wt-012 DA-006: a failed rollback reset can leave
            # the newly staged paths in the index. Surface the
            # dirty-index state in the FAILED error so callers
            # know manual repair may be needed.
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
