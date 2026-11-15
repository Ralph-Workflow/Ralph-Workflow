from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path


class CommitEvidenceSource(Protocol):
    def list_changed_paths(self, repo_root: Path) -> list[str]: ...

    def commit_generation_diff(self, repo_root: Path) -> str: ...


__all__ = ["CommitEvidenceSource"]
