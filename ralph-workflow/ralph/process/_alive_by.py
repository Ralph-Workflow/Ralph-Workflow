"""AliveBy — typed corroboration reasons for child liveness."""

from __future__ import annotations

from enum import StrEnum


class AliveBy(StrEnum):
    """Typed corroboration reasons describing why child work still appears alive."""

    FRESH_PROGRESS = "fresh_progress"
    FRESH_HEARTBEAT_ONLY = "fresh_heartbeat_only"
    STALE_LABEL_ONLY = "stale_label_only"
    OS_DESCENDANT_ONLY_STALE_PROGRESS = "os_descendant_only_stale_progress"


__all__ = ["AliveBy"]
