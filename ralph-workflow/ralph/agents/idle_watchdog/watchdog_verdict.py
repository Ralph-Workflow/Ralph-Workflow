from enum import StrEnum


class WatchdogVerdict(StrEnum):
    CONTINUE = "continue"
    WAITING_ON_CHILD = "waiting_on_child"
    FIRE = "fire"
