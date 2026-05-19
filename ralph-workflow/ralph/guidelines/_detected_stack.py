"""DetectedStack — language and framework composition detected from workspace signatures."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DetectedStack:
    """Language and framework composition detected from workspace signature files."""

    primary_language: str = "Unknown"
    secondary_languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)


__all__ = ["DetectedStack"]
