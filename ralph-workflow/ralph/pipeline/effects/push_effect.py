"""Push pipeline effect."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PushEffect:
    """Effect to push changes to remote.

    Attributes:
        remote: Remote name (default: origin).
        branch: Branch name (optional, uses current branch if not specified).
    """

    remote: str = "origin"
    branch: str | None = None
