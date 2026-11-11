
from __future__ import annotations

from pathlib import Path
from typing import cast

from git import GitCommandError, InvalidGitRepositoryError, Repo
from loguru import logger

from ralph.git.git_command_queue import route_git_method
from ralph.git.hardening import parse_porcelain_z

_STATUS_ARGS: tuple[str, ...] = ("--porcelain", "-z", "--untracked-files=all")


def _porcelain_z_paths(raw: str) -> list[str]:
    paths: list[str] = []
    for entry in parse_porcelain_z(raw):
        paths.append(entry.path)
        if entry.rename_source is not None:
            paths.append(entry.rename_source)
    return [path for path in paths if path]


def path_in_scope(path: str, scopes: tuple[str, ...]) -> bool:
    return any(path.startswith(scope) if scope.endswith("/") else path == scope for scope in scopes)


def _list_dirty_paths(repo: Repo, scope: str) -> list[str]:
    raw = cast(
        "str",
        route_git_method(
            repo,
            "status",
            (*_STATUS_ARGS, "--", scope),
        ),
    )
    return sorted({path for path in _porcelain_z_paths(raw) if path_in_scope(path, (scope,))})


def list_dirty_paths(repo_root: Path | str) -> frozenset[str]:
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
