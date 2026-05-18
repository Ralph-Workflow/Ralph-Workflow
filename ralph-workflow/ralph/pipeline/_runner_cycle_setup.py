"""Cycle baseline setup for the pipeline runner."""

from __future__ import annotations

from typing import TYPE_CHECKING

from git import InvalidGitRepositoryError, Repo

from ralph.pipeline.cycle_baseline import read_cycle_baseline, write_cycle_baseline

if TYPE_CHECKING:
    from pathlib import Path


def write_start_commit_if_absent(workspace_root: Path) -> None:
    if read_cycle_baseline(workspace_root) is not None:
        return
    try:
        repo = Repo(workspace_root)
    except InvalidGitRepositoryError:
        return
    if not repo.head.is_valid():
        return
    write_cycle_baseline(workspace_root, repo.head.commit.hexsha, force=True)
