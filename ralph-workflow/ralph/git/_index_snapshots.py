from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.git.git_command_queue import route_git_method

if TYPE_CHECKING:
    from git import Repo

_GIT_LS_FILES_META_FIELDS: int = 3
_GIT_LS_FILES_PATH_PARTS: int = 2

_GIT_INDEX_STAGE_MERGED: str = "0"

_STAGED_DELETION_SENTINEL: str = "__STAGED_DELETION__"


def _snapshot_pre_staged_index(repo: Repo, paths: list[str]) -> dict[str, str | None]:
    snapshots: dict[str, str | None] = {}
    if not paths:
        return snapshots
    ls_files_raw: object = route_git_method(repo, "ls_files", ("--stage", "--", *paths))
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
    for path in paths:
        if path not in indexed:
            snapshots[path] = _STAGED_DELETION_SENTINEL
    return snapshots


def _restore_pre_staged_index(repo: Repo, snapshots: dict[str, str | None]) -> None:
    for path, entry in snapshots.items():
        if entry == _STAGED_DELETION_SENTINEL:
            _ = cast(
                "None",
                route_git_method(repo, "update_index", ("--force-remove", path)),
            )
            continue
        if entry is None:
            continue
        mode, blob_sha = entry.split(",", 1)
        _ = cast(
            "None",
            route_git_method(
                repo,
                "update_index",
                ("--add", "--cacheinfo", f"{mode},{blob_sha},{path}"),
            ),
        )


def _capture_staged_paths(repo: Repo) -> list[str]:
    diff_output: object = route_git_method(repo, "diff", ("--cached", "--name-only"))
    if not isinstance(diff_output, str):
        return []
    return [path for path in diff_output.splitlines() if path]
