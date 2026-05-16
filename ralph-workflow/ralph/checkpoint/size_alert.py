"""Alert levels for checkpoint size checks."""

from __future__ import annotations

from enum import StrEnum


class _StringEnum(StrEnum):
    """Compat base for string-valued enums across tooling versions."""


class SizeAlert(_StringEnum):
    """Alert level for checkpoint size checks."""

    OK = "ok"
    WARNING = "warning"
    ERROR = "error"
