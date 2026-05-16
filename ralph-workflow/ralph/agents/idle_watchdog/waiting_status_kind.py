from enum import StrEnum


class WaitingStatusKind(StrEnum):
    """Kind of waiting-status event emitted by IdleWatchdog.

    ENTERED: transition into WAITING_ON_CHILD deferral.
    PROGRESS: periodic status update while still waiting (rate-limited).
    SUSPECTED_FROZEN: cumulative wait crossed suspect threshold; child may be frozen.
    EXITED: transition out of WAITING_ON_CHILD (activity or drain resumed).
    HARD_STOP: cumulative ceiling crossed; watchdog about to fire CHILDREN_PERSIST_TOO_LONG.
    """

    ENTERED = "entered"
    PROGRESS = "progress"
    SUSPECTED_FROZEN = "suspected_frozen"
    EXITED = "exited"
    HARD_STOP = "hard_stop"
