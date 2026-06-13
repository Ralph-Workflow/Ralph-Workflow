"""Git cleanup operations for commit hardening.

This module provides deterministic git operations for the commit cleanup phase,
handling file deletion, gitignore updates, and git exclude patterns.
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path, PurePath

from git import InvalidGitRepositoryError, Repo
from loguru import logger


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
    target = (repo_root_path / path).resolve(strict=False)
    try:
        target.relative_to(repo_root_path)
    except ValueError as exc:
        raise ValueError(
            f"Refusing to delete path outside repository root: {relative_path!r}"
        ) from exc
    if target.is_symlink():
        raise ValueError(
            f"Refusing to delete symlink path during commit cleanup: {relative_path!r}"
        )
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

    with suppress(OSError):
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
            with exclude_path.open("a", encoding="utf-8") as f:
                if new_patterns[0]:
                    f.write("\n")
                f.write("\n".join(new_patterns))
            logger.debug("Added {} patterns to .git/info/exclude", len(new_patterns))
    finally:
        repo.close()
