"""Typed outcomes for commit creation."""

from __future__ import annotations

from dataclasses import dataclass

from ralph.git._commit_status import CommitCreationStatus


@dataclass(frozen=True)
class CommitCreationResult:
    """Typed outcome for a commit attempt, including concurrent HEAD changes."""

    status: CommitCreationStatus
    sha: str | None = None
    error: str | None = None

    @classmethod
    def created(cls, sha: str) -> CommitCreationResult:
        return cls(CommitCreationStatus.CREATED, sha=sha)

    @classmethod
    def already_advanced(cls, sha: str) -> CommitCreationResult:
        return cls(CommitCreationStatus.ALREADY_ADVANCED, sha=sha)

    @classmethod
    def failed(cls, error: str) -> CommitCreationResult:
        return cls(CommitCreationStatus.FAILED, error=error)


__all__ = ["CommitCreationResult", "CommitCreationStatus"]
