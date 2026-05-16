"""Supported parallel execution modes."""

from enum import StrEnum


class ParallelExecutionMode(StrEnum):
    """Supported parallel execution modes.

    In v1 only SAME_WORKSPACE is supported. Workers share the single checked-out
    repository root and are isolated only by edit-area path restrictions and
    per-worker artifact namespaces — not by filesystem isolation or separate git checkouts.
    """

    SAME_WORKSPACE = "same_workspace"


__all__ = ["ParallelExecutionMode"]
