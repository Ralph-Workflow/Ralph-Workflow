"""Rebase was not applicable."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RebaseNoOp:
    """Rebase was not applicable (already up-to-date or invalid state)."""

    reason: str
