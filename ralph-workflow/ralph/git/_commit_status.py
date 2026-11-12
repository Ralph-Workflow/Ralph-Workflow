from enum import Enum


class CommitCreationStatus(Enum):
    CREATED = "created"
    ALREADY_ADVANCED = "already_advanced"
    FAILED = "failed"
