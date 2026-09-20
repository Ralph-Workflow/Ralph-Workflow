"""Fresh, factual evidence used by the commit-message pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from ralph.git.operations import list_changed_paths
from ralph.prompts._commit_diff import commit_generation_diff

if TYPE_CHECKING:
    from pathlib import Path

_MEDIUM_CHANGE_AREAS: Final = 3
_MEDIUM_PRODUCTION_FILES: Final = 4


@dataclass(frozen=True)
class CommitEvidenceBundle:
    """A live snapshot of the work a commit may describe."""

    diff: str
    changed_files: tuple[str, ...]
    change_areas: tuple[str, ...]
    verification_hints: tuple[str, ...]
    public_behavior_paths: tuple[str, ...]

    @property
    def message_budget(self) -> str:
        """Return the evidence-derived detail target for message authors."""
        areas = len(self.change_areas)
        production = len(self.public_behavior_paths)
        if areas <= 1 and production <= 1:
            return "small: one focused body point"
        if areas <= _MEDIUM_CHANGE_AREAS and production <= _MEDIUM_PRODUCTION_FILES:
            return "medium: two or three focused body points"
        return "large: cover each material area, risks, and verification"


def build_commit_evidence_bundle(repo_root: Path) -> CommitEvidenceBundle:
    """Build a new evidence snapshot from live Git state; never cache it."""
    changed_files = tuple(sorted(set(list_changed_paths(repo_root))))
    areas = tuple(sorted({path.split("/", 1)[0] for path in changed_files if path}))
    public_paths = tuple(
        path
        for path in changed_files
        if path.startswith(("ralph/", "src/", "app/", "lib/", "api/"))
    )
    return CommitEvidenceBundle(
        diff=commit_generation_diff(repo_root),
        changed_files=changed_files,
        change_areas=areas,
        verification_hints=(),
        public_behavior_paths=public_paths,
    )


__all__ = ["CommitEvidenceBundle", "build_commit_evidence_bundle"]
