from enum import StrEnum


class WatchdogFireReason(StrEnum):
    """Why the watchdog decided to fire.

    IdleWatchdog reasons (in-stream):
      NO_OUTPUT_DEADLINE, CHILDREN_PERSIST_TOO_LONG, SESSION_CEILING_EXCEEDED.
    PostExitWatchdog reasons (post-exit):
      PROCESS_EXIT_HANG, DESCENDANT_HANG.
    """

    NO_OUTPUT_DEADLINE = "no_output_deadline"
    CHILDREN_PERSIST_TOO_LONG = "children_persist_too_long"
    SESSION_CEILING_EXCEEDED = "session_ceiling_exceeded"
    PROCESS_EXIT_HANG = "process_exit_hang"
    DESCENDANT_HANG = "descendant_hang"
