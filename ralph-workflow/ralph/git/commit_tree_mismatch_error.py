from __future__ import annotations

from ralph.git.git_operation_error import GitOperationError

__all__ = ["CommitTreeMismatchError"]


class CommitTreeMismatchError(GitOperationError):
    def __init__(self, message: str) -> None:
        super().__init__("create_commit", message)
