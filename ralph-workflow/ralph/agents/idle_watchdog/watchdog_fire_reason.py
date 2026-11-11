
from enum import StrEnum


class WatchdogFireReason(StrEnum):

    NO_OUTPUT_DEADLINE = "no_output_deadline"
    CONFLICT_INACTIVITY = "conflict_inactivity"
    OPERATOR_CAP_REACHED = "operator_cap_reached"
    NO_OUTPUT_AT_START = "no_output_at_start"
    STALLED_AFTER_TOOL_RESULT = "stalled_after_tool_result"
    REPEATED_ERROR_LOOP = "repeated_error_loop"
    REPEATED_IDENTICAL_TOOL_CALL = "repeated_identical_tool_call"
    CHILDREN_PERSIST_TOO_LONG = "children_persist_too_long"
    NO_PROGRESS_QUIET = "no_progress_quiet"
    STRICTLY_STUCK = "strictly_stuck"
    SESSION_CEILING_EXCEEDED = "session_ceiling_exceeded"
    PROCESS_EXIT_HANG = "process_exit_hang"
    DESCENDANT_HANG = "descendant_hang"
    DEFERRED_BY_STUCK_CLASSIFIER = "deferred_by_stuck_classifier"
