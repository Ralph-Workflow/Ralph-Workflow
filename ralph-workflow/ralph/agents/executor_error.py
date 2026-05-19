"""Executor exception types."""

from __future__ import annotations

__all__ = ["ExecutorError"]


class ExecutorError(Exception):
    """Raised when an executor encounters an unrecoverable failure."""
