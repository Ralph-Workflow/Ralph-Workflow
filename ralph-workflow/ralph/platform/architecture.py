"""Normalized CPU architecture names."""

from __future__ import annotations

from enum import StrEnum


class Architecture(StrEnum):
    """Normalized CPU architecture names."""

    X86_64 = "x86_64"
    ARM64 = "arm64"
    X86 = "x86"
    UNKNOWN = "unknown"
