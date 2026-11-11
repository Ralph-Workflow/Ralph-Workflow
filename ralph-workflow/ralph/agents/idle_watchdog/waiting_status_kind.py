
from enum import StrEnum


class WaitingStatusKind(StrEnum):

    ENTERED = "entered"
    PROGRESS = "progress"
    SUSPECTED_FROZEN = "suspected_frozen"
    EXITED = "exited"
    HARD_STOP = "hard_stop"
    SUBAGENT_PROGRESS = "subagent_progress"
    STALLED = "stalled"
    STALL_RESUMED = "stall_resumed"
