"""Dirty-path discovery helpers for the deterministic auto-commit machinery.

Extracted from :mod:`ralph.git.scoped_auto_commit` (wt-012) to keep that
module under the 1000-line repo-structure cap. ``scoped_auto_commit``
re-exports every public name from here, so existing callers
(``from ralph.git.scoped_auto_commit import ...``) keep working.

The helpers snapshot the working tree via ``git status --porcelain -z
--untracked-files=all``. ``-z`` matters: without it git C-quotes every
path holding a space, a quote or a non-ASCII byte (``"my notes.md"``,
``"caf\\303\\251.txt"``) and joins a rename into one ``old -> new``
string, so the snapshot never matched the real paths callers compare it
against. A rename contributes BOTH of its paths. The helpers:

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

from ralph.git.git_command_queue import route_git_method
from ralph.git.hardening import parse_porcelain_z

#: The ``git status`` arguments every snapshot uses (NUL-delimited, unquoted).
_STATUS_ARGS: tuple[str, ...] = ("--porcelain", "-z", "--untracked-files=all")


def _porcelain_z_paths(raw: str) -> list[str]:
    """Every path a ``git status --porcelain -z`` blob names, unquoted.

    A rename or copy names two paths and both are dirty: the source is
    gone from its old location and the destination is new.
    """
    paths: list[str] = []
    for entry in parse_porcelain_z(raw):
        paths.append(entry.path)
        if entry.rename_source is not None:
            paths.append(entry.rename_source)
    return [path for path in paths if path]


def path_in_scope(path: str, scopes: tuple[str, ...]) -> bool:
    """True when ``path`` falls under any scope (dir prefix or exact file)."""
    return any(path.startswith(scope) if scope.endswith("/") else path == scope for scope in scopes)


def _list_dirty_paths(repo: Repo, scope: str) -> list[str]:
    """Return sorted repo-relative dirty paths under one scope.

    ``--untracked-files=all`` is required so nested untracked files are
    reported individually rather than collapsed to the parent directory.

    The ``repo.git.status`` call is dispatched through
    :func:`route_git_method` so every dirty-path probe acquires the
    shared per-repository :class:`GitCommandQueue` lock — successive
    ``commit_scoped_updates`` probes cannot interleave with a sibling
    chore writer that is staging the same scope.
    """
    raw = cast(
        "str",
        route_git_method(
            repo,
            "status",
            (*_STATUS_ARGS, "--", scope),
        ),
    )  # cast-policy: seam: structural boundary (git stdout probe; CLI returns plain text)
    return sorted({path for path in _porcelain_z_paths(raw) if path_in_scope(path, (scope,))})


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
        raw: object = route_git_method(
            repo,
            "status",
            _STATUS_ARGS,
        )
    except (InvalidGitRepositoryError, GitCommandError, OSError) as exc:
        logger.debug("could not snapshot the working tree ({}); assuming clean", exc)
        return frozenset()
    if not isinstance(raw, str):
        return frozenset()
    return frozenset(_porcelain_z_paths(raw))


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
        raw: object = route_git_method(
            repo,
            "status",
            _STATUS_ARGS,
        )
    except (InvalidGitRepositoryError, GitCommandError, OSError) as exc:
        logger.debug("could not snapshot the working tree strictly ({}); reporting None", exc)
        return None
    if not isinstance(raw, str):
        return None
    return frozenset(_porcelain_z_paths(raw))
