"""Rebase success result."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RebaseSuccess:
    """Rebase completed successfully."""
