from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from git import GitCommandError, InvalidGitRepositoryError, NoSuchPathError, Repo

from ralph.git.git_command_queue import route_git_method

if TYPE_CHECKING:
    from collections.abc import Sequence


def gitignored_paths(repo_root: Path | str, paths: Sequence[str]) -> frozenset[str]:
    if not paths:
        return frozenset()
    try:
        repo = Repo(Path(repo_root))
    except (InvalidGitRepositoryError, NoSuchPathError, OSError, ValueError):
        return frozenset()
    try:
        raw: object = route_git_method(repo, "check_ignore", ("--", *paths))
    except GitCommandError:
        return frozenset()
    finally:
        repo.close()
    return frozenset(line.strip() for line in str(raw).splitlines() if line.strip())


__all__ = ["gitignored_paths"]
