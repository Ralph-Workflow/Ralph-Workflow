from __future__ import annotations

from git.exc import GitCommandError


class GitOperationError(GitCommandError):
    def __init__(self, operation: str, message: str) -> None:
        self.operation = operation
        self.message = message
        super().__init__(operation, 1, stderr=message)

    def __str__(self) -> str:
        return f"Git {self.operation} failed: {self.message}"


class CommitTreeMismatchError(GitOperationError):
    def __init__(self, message: str) -> None:
        super().__init__("create_commit", message)


__all__ = ["CommitTreeMismatchError", "GitOperationError"]
