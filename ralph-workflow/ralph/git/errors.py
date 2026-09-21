"""Typed failures raised by Ralph's Git operations."""

from __future__ import annotations

from git.exc import GitCommandError


class GitOperationError(GitCommandError):
    """Represent a failed high-level Git operation while retaining Git failure semantics."""

    def __init__(self, operation: str, message: str) -> None:
        self.operation = operation
        self.message = message
        super().__init__(operation, 1, stderr=message)

    def __str__(self) -> str:
        return f"Git {self.operation} failed: {self.message}"


__all__ = ["GitOperationError"]
