"""Tripwire finding representation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    """A single tripwire finding.

    Attributes:
        category: Machine-readable category identifying the rule that triggered.
        message: Human-readable diagnostic description of the finding.
        path: Optional repository-relative path associated with the finding.
        count: Optional count or size metric associated with the finding.
    """

    category: str
    message: str
    path: str | None = None
    count: int | None = None
