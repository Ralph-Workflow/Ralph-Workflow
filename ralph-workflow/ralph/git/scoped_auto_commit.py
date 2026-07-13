"""Generic deterministic auto-commit over an explicit path scope.

Shared machinery behind Ralph's engine-owned chore commits — the skill-tree
sync (``ralph.skills._auto_commit``) and the project-policy readiness sync
(``ralph.project_policy._auto_commit``). Each caller supplies its path
scopes, deterministic subject, and body builder; this module owns the git
mechanics that make the commit safe:

* dirty-path discovery per scope via ``git status --porcelain
  --untracked-files=all -- <scope>`` with a defensive re-filter;
* preservation of the user's exact staged state for paths OUTSIDE the
  scope (snapshot via ``git ls-files --stage``, restore via ``git
  update-index --cacheinfo``) so a partially staged file is never
  silently committed or corrupted;
* best-effort semantics: a non-git workspace, a clean scope, or any
  ``OSError`` / ``GitCommandError`` returns ``None`` — a broken git state
  must never block the pipeline.

A scope string ending in ``/`` matches every path under that directory;
any other scope string matches that exact repo-relative file.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

from git import GitCommandError, InvalidGitRepositoryError, Repo
from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Callable


# ``git status --porcelain`` lines start with a 2-char status code followed
# by a single space -- so a valid line has at least 4 characters of prefix
# before the path body begins. Lines shorter than this are noise.
_GIT_PORCELAIN_PREFIX_LEN: int = 4

# ``git ls-files --stage`` returns ``<mode> <blob-sha> <stage>\t<path>``.
_GIT_LS_FILES_META_FIELDS: int = 3
_GIT_LS_FILES_PATH_PARTS: int = 2

# Stage-0 means "fully merged in the index". Anything else indicates an
# unmerged conflict state which the chore commit MUST NOT touch.
_GIT_INDEX_STAGE_MERGED: str = "0"


def path_in_scope(path: str, scopes: tuple[str, ...]) -> bool:
    """True when ``path`` falls under any scope (dir prefix or exact file)."""
    return any(
        path.startswith(scope) if scope.endswith("/") else path == scope
        for scope in scopes
    )


def _list_dirty_paths(repo: Repo, scope: str) -> list[str]:
    """Return sorted repo-relative dirty paths under one scope.

    ``--untracked-files=all`` is required so nested untracked files are
    reported individually rather than collapsed to the parent directory.
    """
    raw = cast("str", repo.git.status("--porcelain", "--untracked-files=all", "--", scope))
    dirty: list[str] = []
    for line in raw.splitlines():
        if len(line) < _GIT_PORCELAIN_PREFIX_LEN:
            continue
        path = line[_GIT_PORCELAIN_PREFIX_LEN - 1 :].strip()
        if path_in_scope(path, (scope,)):
            dirty.append(path)
    return sorted(set(dirty))


def list_dirty_paths(repo_root: Path | str) -> frozenset[str]:
    """Return every dirty repo-relative path in the working tree.

    Used to snapshot the working tree BEFORE an agent runs, so a later call can
    attribute the newly-dirty paths to that agent and commit only those. A path
    the user had already modified is never swept into an automated commit.

    Returns an empty set for a non-git workspace or on any git error: a failure
    to read the tree must degrade to "attribute nothing", never to "attribute
    everything".
    """
    try:
        repo = Repo(Path(repo_root), search_parent_directories=False)
        raw = cast(
            "str", repo.git.status("--porcelain", "--untracked-files=all")
        )
    except (InvalidGitRepositoryError, GitCommandError, OSError) as exc:
        logger.debug("could not snapshot the working tree ({}); assuming clean", exc)
        return frozenset()
    return frozenset(
        line[_GIT_PORCELAIN_PREFIX_LEN - 1 :].strip()
        for line in raw.splitlines()
        if len(line) >= _GIT_PORCELAIN_PREFIX_LEN
    )


def _snapshot_pre_staged_index_entries(
    repo: Repo, paths: list[str]
) -> dict[str, tuple[str, str]]:
    """Capture each path's index entry (mode + blob SHA) via ``git ls-files --stage``.

    Restoring by pathname would replace partially staged hunks with the
    working-tree content; restoring the exact index entry preserves the
    user's staging state byte-for-byte. Unmerged (non-stage-0) entries are
    skipped defensively.
    """
    snapshots: dict[str, tuple[str, str]] = {}
    if not paths:
        return snapshots
    ls_files_raw = cast("str", repo.git.ls_files("--stage", "--", *paths))
    for ls_line in ls_files_raw.splitlines():
        parts = ls_line.split("\t", 1)
        if len(parts) != _GIT_LS_FILES_PATH_PARTS:
            continue
        meta = parts[0].split()
        if len(meta) < _GIT_LS_FILES_META_FIELDS:
            continue
        mode, blob_sha, stage = meta[0], meta[1], meta[2]
        if stage != _GIT_INDEX_STAGE_MERGED:
            continue
        snapshots[parts[1]] = (mode, blob_sha)
    return snapshots


def _restore_pre_staged_index_entries(
    repo: Repo, snapshots: dict[str, tuple[str, str]]
) -> None:
    """Restore each path's exact index entry via ``git update-index --cacheinfo``."""
    for path, (mode, blob_sha) in snapshots.items():
        _ = cast(
            "None",
            repo.git.update_index(
                "--add",
                "--cacheinfo",
                f"{mode},{blob_sha},{path}",
            ),
        )


def commit_scoped_updates(
    repo_root: Path | str,
    *,
    scopes: tuple[str, ...],
    subject: str,
    body_builder: Callable[[list[str]], str],
    create_commit_fn: Callable[[Path | str, str], str],
    stage_fn: Callable[[Path | str, list[str]], None],
    path_filter: Callable[[str], bool] | None = None,
    exclude: frozenset[str] = frozenset(),
) -> str | None:
    """Create one deterministic chore commit for dirty paths inside ``scopes``.

    ``path_filter`` (optional) further narrows the in-scope dirty set — a
    path is committed only when the filter returns True. Callers use it for
    content-conditional scopes (e.g. commit a migration candidate only when
    it carries the migrated marker).

    ``exclude`` is subtracted from the dirty set BEFORE anything else. Callers
    pass the paths that were already dirty before the automated work began, so a
    file the user was mid-edit on is never swept into an automated commit — even
    when it sits inside one of the ``scopes``. Being Ralph-owned (``AGENTS.md``,
    the canonical policy dir) makes a file in-scope; it does NOT make the user's
    uncommitted edit to it ours to commit.

    Returns the commit SHA, or ``None`` when no commit was created (non-git
    workspace, clean scope, or any ``OSError`` / ``GitCommandError``).
    """
    try:
        repo_root_path = Path(repo_root)
        try:
            repo = Repo(repo_root_path)
        except (InvalidGitRepositoryError, Exception):
            logger.debug(
                "commit_scoped_updates: {} is not a git repo; skipping auto-commit",
                repo_root_path,
            )
            return None

        try:
            all_dirty: list[str] = []
            for scope in sorted(scopes):
                all_dirty.extend(_list_dirty_paths(repo, scope))
            # Defensive re-filter: drop anything outside the scopes even if
            # ``git status -- <scope>`` somehow returned it. Then drop everything
            # that was already dirty before we started -- that is the user's work.
            all_dirty = sorted(
                {
                    path
                    for path in all_dirty
                    if path_in_scope(path, scopes) and path not in exclude
                }
            )
            if path_filter is not None:
                all_dirty = [path for path in all_dirty if path_filter(path)]
            if not all_dirty:
                return None
            # Snapshot and unstage every pre-staged OUT-OF-SCOPE path so the
            # chore commit cannot capture unrelated pre-staged entries, then
            # restore the exact index state afterwards.
            pre_staged_outside = sorted(
                path
                for path in cast(
                    "str", repo.git.diff("--cached", "--name-only")
                ).splitlines()
                if not path_in_scope(path, scopes)
            )
            pre_staged_blobs = _snapshot_pre_staged_index_entries(repo, pre_staged_outside)
            if pre_staged_outside:
                cast("None", repo.git.reset("HEAD", "--", *pre_staged_outside))
            try:
                stage_fn(repo_root_path, all_dirty)
                message = f"{subject}\n\n{body_builder(all_dirty)}"
                return create_commit_fn(repo_root_path, message)
            finally:
                # Best-effort restore -- a broken git state MUST NOT block
                # the pipeline. Worst case the user re-runs ``git add``.
                if pre_staged_blobs:
                    try:
                        _restore_pre_staged_index_entries(repo, pre_staged_blobs)
                    except Exception as restore_exc:
                        logger.debug(
                            "commit_scoped_updates: failed to restore pre-staged "
                            "out-of-scope paths (non-fatal): {}",
                            restore_exc,
                        )
        except (OSError, GitCommandError) as exc:
            logger.debug("commit_scoped_updates: auto-commit failed (non-fatal): {}", exc)
            return None
        finally:
            repo.close()
    except (OSError, GitCommandError) as exc:
        logger.debug("commit_scoped_updates: outer guard caught (non-fatal): {}", exc)
        return None


__all__ = [
    "commit_scoped_updates",
    "path_in_scope",
]
