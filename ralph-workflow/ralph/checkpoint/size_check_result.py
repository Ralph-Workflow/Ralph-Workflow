"""Structured results for checkpoint size checks."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SizeCheckResult:
    """Structured checkpoint size check result."""

    level: str
    message: str | None = None
