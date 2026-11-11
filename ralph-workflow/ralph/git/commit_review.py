
from __future__ import annotations

from contextlib import suppress
from fnmatch import fnmatchcase
from pathlib import Path, PurePath, PurePosixPath
from typing import TYPE_CHECKING, Protocol, cast

from git import InvalidGitRepositoryError, Repo
from loguru import logger

from ralph.git.git_command_queue import route_git_method
from ralph.git.operations import _atomic_append_text, stage_all
from ralph.skills._agent_paths import _SKILL_ROOT_PREFIXES

if TYPE_CHECKING:
    from collections.abc import Callable


_TRACKED_SECRET_BASENAME_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.local",
    ".env.*.local",
    "secrets.yml",
    "credentials.json",
)


class _RepoClass(Protocol):
    @classmethod
    def init(cls, path: str | Path) -> _RepoClass: ...

    def close(self) -> None: ...


def ensure_git_initialized(repo_root: Path | str) -> None:
    with suppress(InvalidGitRepositoryError):
        repo = Repo(repo_root, search_parent_directories=False)
        repo.close()
        return
    new_repo = cast("_RepoClass", Repo).init(repo_root)
    try:
        logger.info("Initialized git repository at {}", repo_root)
    finally:
        new_repo.close()


def delete_file_from_repo(repo_root: Path | str, relative_path: str) -> None:
    repo_root_path = Path(repo_root).resolve()
    path = PurePath(relative_path)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise ValueError(f"Refusing to delete path outside repository root: {relative_path!r}")
    unresolved = repo_root_path / path
    if unresolved.is_symlink():
        raise ValueError(f"Refusing to delete symlink path during commit review: {relative_path!r}")
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
                route_git_method(repo, "rm", ("-f", "--cached", "--", relative_path))
        finally:
            repo.close()

    target.unlink(missing_ok=True)
    logger.debug("Deleted file {}", relative_path)


def add_to_git_exclude(repo_root: Path | str, patterns: list[str]) -> None:
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

        sensitive_patterns = [
            pattern
            for pattern in new_patterns
            if _is_recognized_secret_name(PurePosixPath(pattern.strip().replace("\\", "/")).name)
        ]
        if sensitive_patterns:
            tracked_output = cast(
                "str",
                route_git_method(repo, "ls_files", ("--cached", "-z")),
            )
            tracked: list[str] = [path for path in tracked_output.split("\0") if path]
            tracked_secrets: list[str] = [
                path
                for path in tracked
                if _is_recognized_secret_name(PurePosixPath(path).name)
                and any(
                    _sensitive_pattern_matches_path(pattern, path) for pattern in sensitive_patterns
                )
            ]
            if tracked_secrets:
                route_git_method(repo, "rm", ("-f", "--cached", "--", *tracked_secrets))
                logger.debug(
                    "Untracked {} recognized secret file(s) while retaining working-tree copies",
                    len(tracked_secrets),
                )
    finally:
        repo.close()


def _is_recognized_secret_name(name: str) -> bool:
    return any(
        fnmatchcase(name, secret_pattern) for secret_pattern in _TRACKED_SECRET_BASENAME_PATTERNS
    )


def is_recognized_secret_path(path: str) -> bool:
    return _is_recognized_secret_name(PurePosixPath(path.replace("\\", "/")).name)


def _sensitive_pattern_matches_path(pattern: str, tracked_path: str) -> bool:
    normalized = pattern.strip().replace("\\", "/")
    pattern_name = PurePosixPath(normalized).name
    if not _is_recognized_secret_name(pattern_name):
        return False
    if "/" in normalized:
        return fnmatchcase(tracked_path, normalized.lstrip("/"))
    return fnmatchcase(PurePosixPath(tracked_path).name, normalized)


def untrack_engine_internal_files(
    repo_root: Path | str,
    is_internal_path: Callable[[str], bool],
) -> list[str]:
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
            if any(entry_path_str.startswith(prefix) for prefix in _SKILL_ROOT_PREFIXES):
                logger.debug(
                    "Skipping tracked skill-root path (canonical project-scope skills): {}",
                    entry_path_str,
                )
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
                route_git_method(repo, "rm", ("--cached", "--", entry_path_str))
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


def stage_commit_changes_safely(repo_root: Path | str) -> None:
    add_to_git_exclude(repo_root, list(_TRACKED_SECRET_BASENAME_PATTERNS))
    stage_all(repo_root)
