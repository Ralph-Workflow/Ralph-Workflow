"""Normalized operating system names."""

from __future__ import annotations

from enum import StrEnum


class OperatingSystem(StrEnum):
    """Normalized operating system names."""

    MACOS = "macos"
    LINUX = "linux"
    WINDOWS = "windows"
    UNKNOWN = "unknown"
