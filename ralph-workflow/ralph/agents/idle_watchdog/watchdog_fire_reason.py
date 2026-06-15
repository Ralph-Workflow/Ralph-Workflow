"""Enumeration of reasons the idle watchdog fires."""

from enum import StrEnum


class WatchdogFireReason(StrEnum):
    """Why the watchdog decided to fire.

    IdleWatchdog reasons (in-stream):
      NO_OUTPUT_DEADLINE, NO_OUTPUT_AT_START, CHILDREN_PERSIST_TOO_LONG,
      SESSION_CEILING_EXCEEDED.
    PostExitWatchdog reasons (post-exit):
      PROCESS_EXIT_HANG, DESCENDANT_HANG.
    """

    NO_OUTPUT_DEADLINE = "no_output_deadline"
    NO_OUTPUT_AT_START = "no_output_at_start"
    STALLED_AFTER_TOOL_RESULT = "stalled_after_tool_result"
    REPEATED_ERROR_LOOP = "repeated_error_loop"
    CHILDREN_PERSIST_TOO_LONG = "children_persist_too_long"
    NO_PROGRESS_QUIET = "no_progress_quiet"
    SESSION_CEILING_EXCEEDED = "session_ceiling_exceeded"
    PROCESS_EXIT_HANG = "process_exit_hang"
    DESCENDANT_HANG = "descendant_hang"
