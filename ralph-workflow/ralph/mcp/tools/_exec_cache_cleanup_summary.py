"""Summary returned by ExecSandboxManager.cleanup_base()."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecCacheCleanupSummary:
    removed_paths: int
    removed_bytes: int
    remaining_bytes: int


__all__ = ["ExecCacheCleanupSummary"]
