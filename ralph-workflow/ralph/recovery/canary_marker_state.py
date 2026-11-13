from __future__ import annotations

from enum import StrEnum


class CanaryMarkerState(StrEnum):
    MISSING = "marker_missing"
    MISMATCHED = "marker_content_mismatch"
    MATCHED = "marker_written"
    UNREADABLE = "marker_unreadable"
