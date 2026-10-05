"""Dirty-path discovery helpers for the deterministic auto-commit machinery.

Extracted from :mod:`ralph.git.scoped_auto_commit` (wt-012) to keep that
module under the 1000-line repo-structure cap. ``scoped_auto_commit``
re-exports every public name from here, so existing callers
(``from ralph.git.scoped_auto_commit import ...``) keep working.

The helpers snapshot the working tree via ``git status --porcelain
--untracked-files=all``:

* :func:`path_in_scope` / :func:`_list_dirty_paths` — per-scope dirty
  discovery with a defensive re-filter (used by ``commit_scoped_updates``);
* :func:`list_dirty_paths` — whole-tree snapshot that degrades to empty on
  any read failure ("attribute nothing", never "attribute everything");
* :func:`snapshot_dirty_paths_strict` — whole-tree snapshot that returns
  ``None`` on read failure so callers can distinguish clean from broken.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from git import GitCommandError, InvalidGitRepositoryError, Repo
from loguru import logger

# ``git status --porcelain`` lines start with a 2-char status code followed
# by a single space -- so a valid line has at least 4 characters of prefix
# before the path body begins. Lines shorter than this are noise.
_GIT_PORCELAIN_PREFIX_LEN: int = 4


def path_in_scope(path: str, scopes: tuple[str, ...]) -> bool:
    """True when ``path`` falls under any scope (dir prefix or exact file)."""
    return any(path.startswith(scope) if scope.endswith("/") else path == scope for scope in scopes)


def _list_dirty_paths(repo: Repo, scope: str) -> list[str]:
    """Return sorted repo-relative dirty paths under one scope.

    ``--untracked-files=all`` is required so nested untracked files are
    reported individually rather than collapsed to the parent directory.
    """
    raw = cast(
        "str", repo.git.status("--porcelain", "--untracked-files=all", "--", scope)
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
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
        raw: object = repo.git.status("--porcelain", "--untracked-files=all")
    except (InvalidGitRepositoryError, GitCommandError, OSError) as exc:
        logger.debug("could not snapshot the working tree ({}); assuming clean", exc)
        return frozenset()
    if not isinstance(raw, str):
        return frozenset()
    return frozenset(
        line[_GIT_PORCELAIN_PREFIX_LEN - 1 :].strip()
        for line in raw.splitlines()
        if len(line) >= _GIT_PORCELAIN_PREFIX_LEN
    )


def snapshot_dirty_paths_strict(repo_root: Path | str) -> frozenset[str] | None:
    """Snapshot the working tree, distinguishing clean from unreadable.

    Returns:

    * ``frozenset()`` when the working tree is clean (no dirty paths).
    * ``frozenset({...})`` listing the dirty paths.
    * ``None`` when the tree could not be read (non-git workspace, git
      error, OSError). Callers use ``None`` to skip a deterministic
      chore commit with a visible warning, instead of the legacy
      "degrade to empty" path that conflated clean with broken.

    Mirrors :func:`list_dirty_paths` on the read path; the difference is
    the return type (``None`` vs empty ``frozenset``) so callers can
    decide whether to skip with a warning.
    """
    try:
        repo = Repo(Path(repo_root), search_parent_directories=False)
        raw: object = repo.git.status("--porcelain", "--untracked-files=all")
    except (InvalidGitRepositoryError, GitCommandError, OSError) as exc:
        logger.debug("could not snapshot the working tree strictly ({}); reporting None", exc)
        return None
    if not isinstance(raw, str):
        return None
    return frozenset(
        line[_GIT_PORCELAIN_PREFIX_LEN - 1 :].strip()
        for line in raw.splitlines()
        if len(line) >= _GIT_PORCELAIN_PREFIX_LEN
    )
