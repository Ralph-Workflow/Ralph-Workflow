"""Timeout profile enum for ordinary and conflict-only supervision."""

from __future__ import annotations

from enum import StrEnum


class TimeoutProfile(StrEnum):
    """The timeout verdict family an invocation is allowed to use."""

    STANDARD = "standard"
    ACTIVITY_ONLY = "activity_only"


__all__ = ["TimeoutProfile"]
