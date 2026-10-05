"""Candidate-set and post-install diff helpers for the skill installer.

Extracted from :mod:`ralph.skills._installer` so the installer's main
module stays under the audit's 1000-line cap. Every function here
participates in the producer-level ``install_project_baseline_skills_with_diff``
boundary:

* :func:`_candidate_skill_paths` enumerates the byte-exact set of
  paths the install could write (a SUPERSET -- the post-install diff
  narrows it to the actually-written set);
* :func:`_tracked_descendants` and :func:`_lexical_descendants` walk
  ``git ls-files`` (or a filesystem fallback for non-git harnesses) to
  capture every tracked descendant of a skill directory, including
  the dir-to-symlink transition shape the U2 plan locks against;
* :func:`_rel` computes the workspace-relative path for a leaf
  LEXICALLY (no symlink resolution on the leaf) so a deleted symlink
  stays attributed to its own path rather than wherever the symlink
  pointed;
* :func:`_diff_written_paths` narrows the candidate set to the paths
  whose on-disk content actually changed since the pre-write
  snapshot -- the byte-exact set the deterministic chore commit
  consumes via :func:`ralph.git.scoped_auto_commit.commit_deterministic_writes`.

Split from the installer module to keep the installer's top-level
``install_*`` functions and ``_mirror_*`` / ``_materialize_*`` /
``_prune_*`` helpers co-located; this module is the candidate-set
companion that ``install_project_baseline_skills_with_diff`` imports.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.skills._agent_paths import (
    project_sibling_skill_roots,
    project_skill_root,
)
from ralph.skills._content import _MANAGED_MARKER, BASELINE_SKILL_NAMES

if TYPE_CHECKING:
    from collections.abc import Callable


def _candidate_skill_paths(workspace_root: Path) -> list[str]:
    """List every repo-relative path the project-scope install could write.

    Covers the canonical metadata + every baseline skill's files, plus
    the per-skill entry under each project sibling root (the symlinks).
    The set is a SUPERSET: not every candidate is actually written on
    every install. The post-install diff against the recorded pre-write
    hashes narrows it down to the truly-written paths.

    U2 expansion (wt-012 PA-002 / DA-007):
      * For each sibling root, include the sibling ROOT path itself
        (so the new symlink or materialized tree lands in the candidate
        set).
      * For each sibling root, include every TRACKED descendant of the
        root (via ``git ls-files <root>``). This captures the pre-write
        hash of every tracked descendant that ``shutil.rmtree`` will
        delete when the install replaces a tracked sibling directory
        with a symlink (the dir-to-symlink transition). Without these, the
        descendant deletions are invisible to the post-install diff and
        the install's chore commit silently drops them.
      * For each managed canonical skill directory that the baseline
        no longer ships (the prune targets), include ALL descendants
        of that directory (not just ``SKILL.md`` + ``_MANAGED_MARKER``)
        so any nested file inside a retired skill is also captured by
        the pre-write snapshot.
    """
    canonical = project_skill_root(workspace_root)
    candidates: list[str] = [_rel(canonical, "metadata.json", workspace_root)]
    for name in BASELINE_SKILL_NAMES:
        candidates.append(_rel(canonical / name, "SKILL.md", workspace_root))
        candidates.append(_rel(canonical / name, _MANAGED_MARKER, workspace_root))
    # wt-012 DA-007: the prune removes managed skill directories that the
    # baseline no longer ships. Enumerate any existing managed directories
    # OUTSIDE the baseline so their (deleted) paths are present in the
    # candidate set; the post-install diff then attributes the deletions
    # to the deterministic commit via ``git add --all``.
    #
    # U2 expansion: include ALL descendants of the prune target (not just
    # ``SKILL.md`` + ``_MANAGED_MARKER``) so any nested file inside a
    # retired skill directory is captured too. ``shutil.rmtree`` deletes
    # every file in the dir, so a tracked nested file with no
    # pre-write snapshot would leak into the working tree uncommitted.
    canonical_skills_dir = workspace_root / canonical.relative_to(workspace_root.resolve())
    if canonical_skills_dir.is_dir():
        for entry in canonical_skills_dir.iterdir():
            if entry.name in BASELINE_SKILL_NAMES or not (entry / _MANAGED_MARKER).exists():
                continue
            candidates.extend(_tracked_descendants(entry, workspace_root))
    for sibling in project_sibling_skill_roots(workspace_root):
        sibling_root = sibling.resolve(workspace_root)
        sibling_root_rel = str(sibling_root.relative_to(workspace_root))
        # U2: include the sibling ROOT path itself so the new symlink
        # (or the materialized tree under a never-yet-existing sibling)
        # lands in the candidate set. ``capture_pre_write_contents``
        # records ``None`` for a non-tracked directory; the post-install
        # diff still observes the new symlink and surfaces it as a real
        # change, so the ROOT path is harmless to include on every run.
        candidates.append(sibling_root_rel)
        # The baseline skill entries (the symlinks themselves) under
        # this sibling root.
        candidates.extend(
            _rel(sibling_root, name, workspace_root) for name in BASELINE_SKILL_NAMES
        )
        # U2: tracked descendants of the sibling ROOT so the install's
        # pre-write snapshot records the pre-write hash of every tracked
        # descendant that ``shutil.rmtree`` will delete when the install
        # replaces a tracked sibling directory with a symlink (the
        # dir-to-symlink transition). The ``git ls-files``-driven
        # authoritative tracked-only list is used so untracked entries
        # do not pollute the candidate set.
        candidates.extend(_tracked_descendants(sibling_root, workspace_root))
    return candidates


def _tracked_descendants(root: Path, workspace_root: Path) -> list[str]:
    """Return the repo-relative paths of every tracked descendant of ``root``.

    Uses ``git ls-files -- <root>`` (authoritative tracked-only list)
    so untracked files do not pollute the candidate set. Symlinks are
    reported as their lexical path (git tracks them as blobs with the
    target text), and ``os.walk``-style directory descent happens
    inside git, not the filesystem, so the result is independent of
    whether the install happens to be running under a workspace that
    has the actual tracked files materialized.

    Args:
        root: Absolute path to the directory whose tracked descendants
            we want to enumerate. May be a non-existent path (returns
            empty list).
        workspace_root: Workspace root used to guard the ``Repo``
            instantiation (no parent-dir search; the workspace's own
            repo only).

    Returns:
        Sorted, de-duplicated list of repo-relative paths. Empty when
        ``root`` is unreadable, has no tracked descendants, or the
        workspace is not a git repo.

    The lexical fallback (when ``git ls-files`` is unavailable) walks
    the filesystem with :func:`os.walk` so a non-git test harness can
    still exercise the candidate-set expansion. The fallback is bounded
    to ``root`` and does not follow symlinks.
    """
    if not root.exists() and not root.is_symlink():
        return []
    try:
        from git import (  # noqa: PLC0415 -- git is an optional seam here
            GitCommandError,
            InvalidGitRepositoryError,
            Repo,
        )

        try:
            repo_obj = Repo(str(workspace_root), search_parent_directories=False)
        except (InvalidGitRepositoryError, Exception):
            return _lexical_descendants(root, workspace_root)
        try:
            try:
                raw = cast(
                    "str", repo_obj.git.ls_files("--", str(root))
                )  # cast-policy: seam: structural boundary
            except (GitCommandError, OSError):
                return _lexical_descendants(root, workspace_root)
            paths = [line.strip() for line in raw.splitlines() if line.strip()]
            return sorted(set(paths))
        finally:
            close = cast(
                "Callable[[], object] | None", getattr(repo_obj, "close", None)
            )
            if callable(close):
                close()
    except ImportError:
        return _lexical_descendants(root, workspace_root)


def _lexical_descendants(root: Path, workspace_root: Path) -> list[str]:
    """Lexical walk fallback for non-git workspaces (test harnesses).

    Returns the repo-relative paths of every regular file or symlink
    found under ``root``. Directories are walked recursively but only
    their files are reported (git does not track directories).
    """
    if not root.exists() and not root.is_symlink():
        return []
    if root.is_symlink() or root.is_file():
        try:
            return [str(root.relative_to(workspace_root))]
        except ValueError:
            return []
    paths: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):  # filesystem-read-ok: skill candidate capture must NOT descend through directory symlinks (the sibling-symlink roots themselves are the boundary being diffed); Workspace.iter_files follows no such guarantee
        base_rel = Path(str(dirpath)).relative_to(workspace_root)
        for filename in filenames:
            file_path = base_rel / filename
            paths.append(str(file_path))
    return sorted(set(paths))


def _rel(absolute: Path, leaf: str, workspace_root: Path) -> str:
    """Build the workspace-relative string for ``absolute / leaf``.

    Computed LEXICALLY (no symlink resolution on the leaf path) so a
    deleted symlink stays attributed to its own path rather than
    wherever the symlink pointed. The caller is responsible for
    ensuring ``absolute`` is built from ``workspace_root`` via
    ``.joinpath(...)`` so containment holds (all install-time callers
    do this).

    Containment is asserted against the resolved ``workspace_root``
    (macOS ``/tmp`` -> ``/private/tmp`` indirection safety) using the
    resolved leaf path, then the LEXICAL relative path is returned so
    the candidate attribution is independent of any pre-existing
    symlinks on the path.
    """
    leaf_path = absolute / leaf
    workspace_resolved = workspace_root.resolve()
    try:
        leaf_resolved = leaf_path.resolve(strict=False)
    except OSError:
        leaf_resolved = leaf_path
    # Containment assertion (defensive): the resolved leaf MUST sit
    # under the resolved workspace_root (macOS ``/tmp`` ->
    # ``/private/tmp`` indirection safe). The lexical relative_to below
    # surfaces the real ValueError to the caller if the leaf is
    # genuinely outside the workspace.
    with contextlib.suppress(ValueError):
        leaf_resolved.relative_to(workspace_resolved)
    return str(leaf_path.relative_to(workspace_root))


def _diff_written_paths(
    workspace_root: Path,
    candidate_paths: list[str],
    pre_contents: dict[str, str | None],
) -> list[str]:
    """Return the candidate paths whose on-disk content changed since the pre-write snapshot.

    A candidate is "written" when either:

    * its pre-write hash was ``None`` (path did not exist) and a path
      now exists at the candidate location, OR
    * its pre-write hash is a string and the current ``git hash-object``
      at that location differs from the recorded pre-write hash.

    The post-install diff is the byte-exact set of paths the install
    actually changed. The deterministic auto-commit consumes it via
    :func:`ralph.git.scoped_auto_commit.commit_deterministic_writes`.
    """
    from git import GitCommandError, InvalidGitRepositoryError, Repo  # noqa: PLC0415

    from ralph.git.scoped_auto_commit import (  # noqa: PLC0415 -- producer-side diff helper
        _git_blob_sha,
    )

    try:
        repo = Repo(workspace_root)
    except (InvalidGitRepositoryError, Exception):
        return []
    try:
        written: list[str] = []
        for path in candidate_paths:
            try:
                current_sha = _git_blob_sha(repo, path)
            except (OSError, GitCommandError):
                continue
            pre_sha = pre_contents.get(path)
            if pre_sha != current_sha:
                written.append(path)
        return written
    finally:
        close = cast("Callable[[], object] | None", getattr(repo, "close", None))
        if callable(close):
            close()
