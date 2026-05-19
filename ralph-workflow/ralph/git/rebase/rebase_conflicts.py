"""Rebase stopped due to conflicts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RebaseConflicts:
    """Rebase stopped because conflicts remain."""

    files: list[str]
