from enum import StrEnum


class WatchdogVerdict(StrEnum):
    """Result of a watchdog evaluation cycle."""

    CONTINUE = "continue"
    WAITING_ON_CHILD = "waiting_on_child"
    FIRE = "fire"
