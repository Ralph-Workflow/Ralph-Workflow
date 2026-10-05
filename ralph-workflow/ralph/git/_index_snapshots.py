"""Pre-staged index snapshot / restore helpers for scoped chore commits.

Extracted from ``ralph.git.scoped_auto_commit`` to keep that module
under the 1000-line repo-structure cap. These helpers capture the
index state of paths the USER pre-staged before a deterministic chore
commit runs, and restore it byte-for-byte on every outcome (CREATED or
FAILED) so the chore commit never destroys user-staged work (wt-012).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from git import Repo

# ``git ls-files --stage`` returns ``<mode> <blob-sha> <stage>\t<path>``.
_GIT_LS_FILES_META_FIELDS: int = 3
_GIT_LS_FILES_PATH_PARTS: int = 2

# Stage-0 means "fully merged in the index". Anything else indicates an
# unmerged conflict state which the chore commit MUST NOT touch.
_GIT_INDEX_STAGE_MERGED: str = "0"

# Sentinel: a path was in ``git diff --cached --name-only`` but absent
# from ``git ls-files --stage`` -> the user staged a deletion. Restore
# by ``git update-index --force-remove <path>`` to keep the index and
# the file in sync with the pre-staged state.
_STAGED_DELETION_SENTINEL: str = "__STAGED_DELETION__"


def _snapshot_pre_staged_index(
    repo: Repo, paths: list[str]
) -> dict[str, str | None]:
    """Capture each pre-staged path's index entry, including staged deletions.

    A path the user pre-staged for a modification has an index entry
    ``<mode> <blob> <stage>\t<path>`` -- we record ``"<mode>,<blob>"``
    so the restore can replay it with ``git update-index --cacheinfo``.

    A path the user pre-staged for a deletion appears in
    ``git diff --cached --name-only`` but NOT in
    ``git ls-files --stage``. We record the
    :data:`_STAGED_DELETION_SENTINEL` so the restore knows to call
    ``git update-index --force-remove <path>`` -- without that call,
    the user's staged deletion would silently revert to the
    pre-deletion index entry after the chore commit.

    Unmerged (non-stage-0) entries are skipped defensively (a chore
    commit MUST NOT touch an unmerged index).
    """
    snapshots: dict[str, str | None] = {}
    if not paths:
        return snapshots
    ls_files_raw: object = repo.git.ls_files("--stage", "--", *paths)
    if not isinstance(ls_files_raw, str):
        return snapshots
    indexed: set[str] = set()
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
        snapshots[parts[1]] = f"{mode},{blob_sha}"
        indexed.add(parts[1])
    # Any pre-staged path NOT in the index is a staged deletion.
    for path in paths:
        if path not in indexed:
            snapshots[path] = _STAGED_DELETION_SENTINEL
    return snapshots


def _restore_pre_staged_index(repo: Repo, snapshots: dict[str, str | None]) -> None:
    """Restore each pre-staged path's exact index state, including staged deletions.

    For an existing index entry, replay it via
    ``git update-index --add --cacheinfo <mode>,<blob>,<path>``. For a
    staged deletion (the :data:`_STAGED_DELETION_SENTINEL` value), force
    the path out of the index with ``git update-index --force-remove``.
    """
    for path, entry in snapshots.items():
        if entry == _STAGED_DELETION_SENTINEL:
            _ = cast("None", repo.git.update_index("--force-remove", path))
            continue
        if entry is None:
            continue
        mode, blob_sha = entry.split(",", 1)
        _ = cast(
            "None",
            repo.git.update_index(
                "--add",
                "--cacheinfo",
                f"{mode},{blob_sha},{path}",
            ),
        )


def _capture_staged_paths(repo: Repo) -> list[str]:
    """Return the list of pre-staged paths (``git diff --cached --name-only``)."""
    diff_output: object = repo.git.diff("--cached", "--name-only")
    if not isinstance(diff_output, str):
        return []
    return [path for path in diff_output.splitlines() if path]
