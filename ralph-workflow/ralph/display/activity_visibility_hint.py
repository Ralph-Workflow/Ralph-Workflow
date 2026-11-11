
from enum import StrEnum


class ActivityVisibilityHint(StrEnum):

    VISIBLE = "visible"
    HIDDEN = "hidden"
    FALLBACK_ONLY = "fallback_only"


__all__ = ["ActivityVisibilityHint"]
