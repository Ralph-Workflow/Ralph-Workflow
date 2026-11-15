from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.git.operations import list_changed_paths
from ralph.prompts._commit_diff import commit_generation_diff

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class GitCommitEvidenceSource:
    def list_changed_paths(self, repo_root: Path) -> list[str]:
        return list_changed_paths(repo_root)

    def commit_generation_diff(self, repo_root: Path) -> str:
        return commit_generation_diff(repo_root)
