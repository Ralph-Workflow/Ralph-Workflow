"""Enumeration of reasons the idle watchdog fires."""

from enum import StrEnum


class WatchdogFireReason(StrEnum):
    """Why the watchdog decided to fire.

    IdleWatchdog reasons (in-stream):
      NO_OUTPUT_DEADLINE, NO_OUTPUT_AT_START, CHILDREN_PERSIST_TOO_LONG,
      SESSION_CEILING_EXCEEDED, REPEATED_IDENTICAL_TOOL_CALL,
      STRICTLY_STUCK.
    PostExitWatchdog reasons (post-exit):
      PROCESS_EXIT_HANG, DESCENDANT_HANG.
    """

    NO_OUTPUT_DEADLINE = "no_output_deadline"
    NO_OUTPUT_AT_START = "no_output_at_start"
    STALLED_AFTER_TOOL_RESULT = "stalled_after_tool_result"
    REPEATED_ERROR_LOOP = "repeated_error_loop"
    REPEATED_IDENTICAL_TOOL_CALL = "repeated_identical_tool_call"
    CHILDREN_PERSIST_TOO_LONG = "children_persist_too_long"
    NO_PROGRESS_QUIET = "no_progress_quiet"
    # Orthogonal ceiling for stuck-but-alive jobs. Fires when the
    # corroborator reports ``alive_by`` in
    # ``{OS_DESCENDANT_ONLY_STALE_PROGRESS, CPU_IDLE_WHILE_ALIVE,
    # LOG_STALE_WHILE_ALIVE}`` AND no first-party channel is fresh
    # for ``no_progress_quiet_strictly_stuck_seconds``. Independent of
    # ``NO_PROGRESS_QUIET`` (which requires ``alive_by is None``).
    # The import-time assertion in idle_watchdog.py locks the enum
    # set so a future PR cannot silently drop this value.
    STRICTLY_STUCK = "strictly_stuck"
    SESSION_CEILING_EXCEEDED = "session_ceiling_exceeded"
    PROCESS_EXIT_HANG = "process_exit_hang"
    DESCENDANT_HANG = "descendant_hang"
    # Diagnostic-only reason. The watchdog NEVER produces FIRE for this
    # reason; it is the StuckKind that the classifier returned when a
    # candidate fire was deferred. Surfaced on the watchdog's
    # last_fire_reason property for post-mortem diagnostics so an
    # operator can see WHY a would-be fire was deferred. The
    # import-time assertion in idle_watchdog.py locks the enum set so a
    # future PR cannot silently add or remove a reason.
    DEFERRED_BY_STUCK_CLASSIFIER = "deferred_by_stuck_classifier"
