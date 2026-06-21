"""Git cleanup operations for commit hardening.

This module provides deterministic git operations for the commit cleanup phase,
handling file deletion, gitignore updates, and git exclude patterns.
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path, PurePath
from typing import TYPE_CHECKING

from git import InvalidGitRepositoryError, Repo
from loguru import logger

from ralph.git.operations import _atomic_append_text

if TYPE_CHECKING:
    from collections.abc import Callable


def ensure_git_initialized(repo_root: Path | str) -> None:
    """Ensure the directory is a git repository, initializing if necessary.

    Args:
        repo_root: Path to the repository root.
    """
    with suppress(InvalidGitRepositoryError):
        repo = Repo(repo_root, search_parent_directories=False)
        repo.close()
        return
    new_repo: Repo = Repo.init(repo_root)  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    try:
        logger.info("Initialized git repository at {}", repo_root)
    finally:
        new_repo.close()


def delete_file_from_repo(repo_root: Path | str, relative_path: str) -> None:
    """Remove a file from the repository, unstaging if necessary.

    Args:
        repo_root: Path to the repository root.
        relative_path: Path relative to repo_root of the file to delete.
    """
    repo_root_path = Path(repo_root).resolve()
    path = PurePath(relative_path)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise ValueError(f"Refusing to delete path outside repository root: {relative_path!r}")
    unresolved = (repo_root_path / path)
    if unresolved.is_symlink():
        raise ValueError(
            f"Refusing to delete symlink path during commit cleanup: {relative_path!r}"
        )
    target = unresolved.resolve(strict=False)
    try:
        target.relative_to(repo_root_path)
    except ValueError as exc:
        raise ValueError(
            f"Refusing to delete path outside repository root: {relative_path!r}"
        ) from exc
    if not target.exists():
        logger.debug("File {} does not exist, nothing to delete", relative_path)
        return

    with suppress(InvalidGitRepositoryError):
        repo = Repo(repo_root_path)
        try:
            tracked_in_index = any(
                entry_path == relative_path for entry_path, _stage in repo.index.entries
            )
            if tracked_in_index:
                repo.git.rm("-f", "--cached", "--", relative_path)
        finally:
            repo.close()

    target.unlink(missing_ok=True)
    logger.debug("Deleted file {}", relative_path)


def add_to_git_exclude(repo_root: Path | str, patterns: list[str]) -> None:
    """Append patterns to .git/info/exclude for machine-local excludes.

    Args:
        repo_root: Path to the repository root.
        patterns: List of patterns to add to exclude.
    """
    repo = Repo(repo_root, search_parent_directories=False)
    try:
        exclude_path = Path(repo.git_dir) / "info" / "exclude"
        exclude_path.parent.mkdir(parents=True, exist_ok=True)

        existing: set[str] = set()
        if exclude_path.exists():
            existing = set(exclude_path.read_text().splitlines())

        new_patterns = [p for p in patterns if p not in existing]
        if new_patterns:
            payload = "\n".join(new_patterns) + "\n"
            _atomic_append_text(exclude_path, payload)
            logger.debug("Added {} patterns to .git/info/exclude", len(new_patterns))
    finally:
        repo.close()


def untrack_engine_internal_files(
    repo_root: Path | str,
    is_internal_path: Callable[[str], bool],
) -> list[str]:
    """Pre-emptively ``git rm --cached`` every tracked engine-internal file.

    This is the rock-solid safety net that the ``commit_cleanup`` phase
    calls BEFORE the agent runs: any tracked file that matches
    ``is_internal_path`` (the canonical ``is_agent_internal_path``
    predicate) is removed from the index so it cannot enter the diff
    the agent sees. Without this step, a ``delete_file`` action for a
    tracked engine-internal file is rejected by ``_is_safe_to_delete``
    (the prior failure mode -- the safety check would otherwise
    surface a hard fail for tracked engine-owned paths even though
    the path is engine-owned).

    Contract:

    * ``git rm --cached`` is used (NOT ``git rm``) -- the working-tree
      files remain on disk so the agent can decide whether to follow
      up with a separate ``delete_file`` action.
    * Symlinks are rejected BEFORE ``git rm --cached`` by checking
      ``Path(repo_root / path).is_symlink()``. ``git rm`` follows
      symlinks, so a symlink under ``.agent/`` could stage the
      symlink target (which may live outside the repo).
    * ``Repo`` is opened in a ``try/finally`` and closed on every
      exit path, mirroring ``delete_file_from_repo``.
    * Per-path failures are wrapped in ``try/except Exception`` so a
      single bad entry does NOT abort the batch -- the helper is
      best-effort by design.
    * The function returns the list of paths actually untracked so
      the caller (``handle_commit_cleanup_phase``) can log the result.

    The placement of this helper's call in
    ``handle_commit_cleanup_phase`` is pinned by the
    ``_check_pre_emptive_untrack_placement`` AST helper in
    ``ralph/testing/audit_agent_internal_paths.py`` -- any future
    refactor that moves the call behind the artifact load, or that
    widens the deletion surface, fails that audit.

    Args:
        repo_root: Path to the repository root. Accepts both ``Path``
            and ``str`` for parity with the rest of this module's API.
        is_internal_path: Predicate that returns True when a tracked
            path is a Ralph runtime artifact (the canonical
            ``is_agent_internal_path`` from
            ``ralph.phases._agent_internal_paths``). Passed as a
            positional argument to keep the helper decoupled from
            the leaf module and avoid circular-import risk.

    Returns:
        List of repository-relative paths that were removed from the
        index. Empty when no paths matched the predicate, when the
        repository has no tracked files, when ``repo_root`` is not a
        git repository, or when every match was a symlink and got
        rejected before ``git rm --cached``.
    """
    repo_root_path = Path(repo_root)
    try:
        repo = Repo(repo_root_path)
    except (InvalidGitRepositoryError, Exception):
        logger.debug(
            "untrack_engine_internal_files: cannot open repo at {}; skipping pre-emptive untrack",
            repo_root_path,
        )
        return []

    untracked: list[str] = []
    try:
        for entry_key in list(repo.index.entries.keys()):
            entry_path = entry_key[0] if isinstance(entry_key, tuple) else entry_key
            entry_path_str = str(entry_path)
            try:
                working_tree_path = repo_root_path / entry_path_str
            except TypeError:
                continue
            if working_tree_path.is_symlink():
                logger.warning(
                    "Refusing to git rm --cached symlink under tracked engine-internal path: {}",
                    entry_path_str,
                )
                continue
            if not is_internal_path(entry_path_str):
                continue
            try:
                repo.git.rm("--cached", "--", entry_path_str)
                untracked.append(entry_path_str)
                logger.debug(
                    "Pre-emptively untracked tracked engine-internal file: {}",
                    entry_path_str,
                )
            except Exception as exc:
                logger.warning(
                    "Pre-emptive git rm --cached failed for {} (continuing batch): {}",
                    entry_path_str,
                    exc,
                )
    finally:
        repo.close()

    return untracked

