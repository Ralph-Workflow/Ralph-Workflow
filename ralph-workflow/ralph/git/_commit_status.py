"""Commit creation outcome categories."""

from enum import Enum


class CommitCreationStatus(Enum):
    """Outcome category returned by commit creation."""

    CREATED = "created"
    ALREADY_ADVANCED = "already_advanced"
    FAILED = "failed"
