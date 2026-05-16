"""Visibility hints for activity event presentation."""

from enum import StrEnum


class ActivityVisibilityHint(StrEnum):
    """Visibility intent used by later presenter and display layers."""

    VISIBLE = "visible"
    HIDDEN = "hidden"
    FALLBACK_ONLY = "fallback_only"


__all__ = ["ActivityVisibilityHint"]
