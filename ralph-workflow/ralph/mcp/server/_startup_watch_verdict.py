
from __future__ import annotations

from enum import StrEnum


class StartupVerdict(StrEnum):

    WAITING = "waiting"
    BOOT_DEADLINE_EXCEEDED = "boot_deadline_exceeded"
    ENVELOPE_DEADLINE_EXCEEDED = "startup_deadline_exceeded"


__all__ = ["StartupVerdict"]
