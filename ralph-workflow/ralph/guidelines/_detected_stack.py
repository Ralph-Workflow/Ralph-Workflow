from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DetectedStack:
    primary_language: str = "Unknown"
    secondary_languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)


__all__ = ["DetectedStack"]
