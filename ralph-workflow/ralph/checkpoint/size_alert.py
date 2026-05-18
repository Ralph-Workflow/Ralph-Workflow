"""Alert levels for checkpoint size checks."""

from __future__ import annotations

from enum import StrEnum


class SizeAlert(StrEnum):
    """Alert level for checkpoint size checks."""

    OK = "ok"
    WARNING = "warning"
    ERROR = "error"
