"""Timeout policy configuration for the idle watchdog."""

from __future__ import annotations

from dataclasses import dataclass

from ralph.timeout_defaults import (
    DESCENDANT_WAIT_POLL_SECONDS,
    DESCENDANT_WAIT_TIMEOUT_SECONDS,
    DRAIN_WINDOW_SECONDS,
    IDLE_POLL_INTERVAL_SECONDS,
    MAX_WAITING_ON_CHILD_NO_PROGRESS_SECONDS,
    MAX_WAITING_ON_CHILD_SECONDS,
    PARENT_EXIT_GRACE_SECONDS,
    POST_TOOL_RESULT_PROGRESSION_SECONDS,
    PROCESS_EXIT_WAIT_SECONDS,
    REPEATED_ERROR_CONSECUTIVE_THRESHOLD,
    REPEATED_ERROR_WINDOW_COUNT,
    REPEATED_ERROR_WINDOW_SECONDS,
    SUSPECT_WAITING_ON_CHILD_SECONDS,
    WAITING_STATUS_INTERVAL_SECONDS,
)


@dataclass(frozen=True)
class TimeoutPolicy:
    """Consolidated timeout configuration for all agent timeout dimensions.

    All timeout constants that previously appeared as module-level magic numbers
    in invoke.py are consolidated here so a single config-built TimeoutPolicy
    governs every timeout decision.

    Precedence of fire conditions (in evaluation order):

    1. SESSION_CEILING_EXCEEDED — absolute wall-clock cap; activity cannot reset it.
    2. NO_OUTPUT_DEADLINE (+ drain window) — idle deadline since last output.
    3. CHILDREN_PERSIST_TOO_LONG — cumulative WAITING_ON_CHILD ceiling; this is an
       absolute ceiling across the session and never decays.
    4. PROCESS_EXIT_HANG — subprocess closed stdout but did not exit within budget.
    5. DESCENDANT_HANG — descendant-wait deadline elapsed with persistent WAITING_ON_CHILD
       (post-exit only, owned by PostExitWatchdog).

    Suspicion is purely informational and does NOT affect any fire condition. The
    ``suspect_waiting_on_child_seconds`` threshold exists only to emit an elevated
    warning event before the hard stop; crossing it never shortens the hard-stop
    ceiling.

    Attributes:
        idle_timeout_seconds: Maximum seconds without output before watchdog may fire.
            None disables the idle-timeout watchdog entirely.
        drain_window_seconds: After a potential timeout, the watchdog enters a drain
            window of this duration to allow late output to flush.
        max_waiting_on_child_seconds: Hard cumulative ceiling on time spent in
            WAITING_ON_CHILD state across the entire session. Activity cannot decay
            or reset it; once exceeded, fires CHILDREN_PERSIST_TOO_LONG even while
            children are still alive.
        max_session_seconds: Absolute wall-clock ceiling for the entire session.
            Activity cannot reset this ceiling. None means no ceiling (opt-in).
            When set, must be >= idle_timeout_seconds.
        idle_poll_interval_seconds: How often the read loop polls for new lines.
            Values < 0.01s are intended for tests only.
        parent_exit_grace_seconds: Grace window after parent rc=0 exit during which
            we poll for late completion signals or appearing children.
        descendant_wait_timeout_seconds: Maximum time to wait for descendant processes
            to finish before declaring failure.
        descendant_wait_poll_seconds: Poll interval for descendant-wait and
            process-exit-wait loops. Values < 0.01s are intended for tests only.
        process_exit_wait_seconds: Maximum time to wait for a subprocess to exit after
            its stdout closes. Prevents hanging on subprocesses that close stdout but
            never call exit().
        waiting_status_interval_seconds: How often to emit a PROGRESS status event
            while WAITING_ON_CHILD deferral is active. Controls only the status
            emission cadence; does NOT affect timeout safety or ceiling math.
        suspect_waiting_on_child_seconds: Cumulative WAITING time after which a
            SUSPECTED_FROZEN event is emitted. Purely informational — does NOT
            shorten the hard-stop ceiling or change the watchdog verdict.
            Must be strictly less than max_waiting_on_child_seconds when set.
            None disables suspicion events.
        max_waiting_on_child_no_progress_seconds: Hard ceiling on cumulative
            WAITING_ON_CHILD time when corroboration shows the child is alive but
            not making progress (e.g., heartbeat-only, stale-label, or OS-descendant-only
            evidence). When set, must be <= max_waiting_on_child_seconds. When None,
            the no-progress ceiling is disabled and max_waiting_on_child_seconds is
            used for all WAITING_ON_CHILD states.
        post_tool_result_progression_seconds: When set, the watchdog fires
            STALLED_AFTER_TOOL_RESULT if no follow-up STREAM_DELTA / OUTPUT_LINE
            activity arrives within this many seconds of a TOOL_RESULT activity.
            This is a NEW BEHAVIOR for direct wedge detection: pre-fix, the
            watchdog only fired NO_OUTPUT_DEADLINE at the full
            idle_timeout_seconds deadline, which meant a post-tool-result
            wedge was detected in ~300s (the default idle timeout) rather
            than ~120s (the default post-tool-result budget). When None,
            the legacy NO_OUTPUT_DEADLINE-only behavior is preserved.
            Must be > 0 when set.
    """

    idle_timeout_seconds: float | None
    drain_window_seconds: float = DRAIN_WINDOW_SECONDS
    max_waiting_on_child_seconds: float = MAX_WAITING_ON_CHILD_SECONDS
    max_session_seconds: float | None = None
    idle_poll_interval_seconds: float = IDLE_POLL_INTERVAL_SECONDS
    parent_exit_grace_seconds: float = PARENT_EXIT_GRACE_SECONDS
    descendant_wait_timeout_seconds: float = DESCENDANT_WAIT_TIMEOUT_SECONDS
    descendant_wait_poll_seconds: float = DESCENDANT_WAIT_POLL_SECONDS
    process_exit_wait_seconds: float = PROCESS_EXIT_WAIT_SECONDS
    waiting_status_interval_seconds: float = WAITING_STATUS_INTERVAL_SECONDS
    suspect_waiting_on_child_seconds: float | None = SUSPECT_WAITING_ON_CHILD_SECONDS
    max_waiting_on_child_no_progress_seconds: float | None = (
        MAX_WAITING_ON_CHILD_NO_PROGRESS_SECONDS
    )
    # When set, the watchdog fires STALLED_AFTER_TOOL_RESULT if no
    # follow-up STREAM_DELTA/OUTPUT_LINE activity arrives within this
    # many seconds of a TOOL_RESULT activity. When None, the legacy
    # NO_OUTPUT_DEADLINE-only behavior is preserved (the wedge is
    # detected only at the full idle_timeout_seconds deadline). The
    # default of 120.0s is generous enough to cover the typical 60s
    # 95th-percentile tool-result-to-output-line latency in
    # production while still detecting the wedge in ~120s rather
    # than waiting for the 300s default.
    post_tool_result_progression_seconds: float | None = (
        POST_TOOL_RESULT_PROGRESSION_SECONDS
    )
    # Repeated-error circuit breaker thresholds. The watchdog fires
    # REPEATED_ERROR_LOOP when an agent re-emits the same error fingerprint
    # either ``repeated_error_consecutive_threshold`` times in a row with no
    # intervening forward progress, or ``repeated_error_window_count`` times
    # within ``repeated_error_window_seconds``. Each rule is independently
    # disablable by setting its threshold to None.
    repeated_error_consecutive_threshold: int | None = REPEATED_ERROR_CONSECUTIVE_THRESHOLD
    repeated_error_window_count: int | None = REPEATED_ERROR_WINDOW_COUNT
    repeated_error_window_seconds: float | None = REPEATED_ERROR_WINDOW_SECONDS

    def __post_init__(self) -> None:
        self._validate_idle_fields()
        self._validate_session_and_poll_fields()
        self._validate_waiting_status_fields()
        self._validate_post_tool_result_progression()
        self._validate_repeated_error_fields()

    def _validate_idle_fields(self) -> None:
        if self.idle_timeout_seconds is not None and self.idle_timeout_seconds <= 0:
            msg = "idle_timeout_seconds must be positive"
            raise ValueError(msg)
        if self.drain_window_seconds < 0:
            msg = "drain_window_seconds must be >= 0"
            raise ValueError(msg)
        if (
            self.idle_timeout_seconds is not None
            and self.max_waiting_on_child_seconds < self.idle_timeout_seconds
        ):
            msg = "max_waiting_on_child_seconds must be >= idle_timeout_seconds when both set"
            raise ValueError(msg)

    def _validate_session_and_poll_fields(self) -> None:
        if self.max_session_seconds is not None and self.max_session_seconds <= 0:
            msg = "max_session_seconds must be positive"
            raise ValueError(msg)
        if (
            self.max_session_seconds is not None
            and self.idle_timeout_seconds is not None
            and self.max_session_seconds < self.idle_timeout_seconds
        ):
            msg = "max_session_seconds must be >= idle_timeout_seconds"
            raise ValueError(msg)
        if self.idle_poll_interval_seconds <= 0:
            msg = "idle_poll_interval_seconds must be positive"
            raise ValueError(msg)
        if self.parent_exit_grace_seconds < 0:
            msg = "parent_exit_grace_seconds must be >= 0"
            raise ValueError(msg)
        if self.descendant_wait_timeout_seconds < 0:
            msg = "descendant_wait_timeout_seconds must be >= 0"
            raise ValueError(msg)
        if self.descendant_wait_poll_seconds <= 0:
            msg = "descendant_wait_poll_seconds must be positive"
            raise ValueError(msg)
        if self.process_exit_wait_seconds < 0:
            msg = "process_exit_wait_seconds must be >= 0"
            raise ValueError(msg)

    def _validate_waiting_status_fields(self) -> None:
        if self.waiting_status_interval_seconds <= 0:
            msg = "waiting_status_interval_seconds must be positive"
            raise ValueError(msg)
        if self.suspect_waiting_on_child_seconds is not None:
            if self.suspect_waiting_on_child_seconds <= 0:
                msg = "suspect_waiting_on_child_seconds must be positive"
                raise ValueError(msg)
            if self.suspect_waiting_on_child_seconds >= self.max_waiting_on_child_seconds:
                msg = (
                    "suspect_waiting_on_child_seconds must be strictly less than"
                    " max_waiting_on_child_seconds"
                )
                raise ValueError(msg)
        if self.max_waiting_on_child_no_progress_seconds is not None:
            if self.max_waiting_on_child_no_progress_seconds <= 0:
                msg = "max_waiting_on_child_no_progress_seconds must be positive"
                raise ValueError(msg)
            if self.max_waiting_on_child_no_progress_seconds > self.max_waiting_on_child_seconds:
                msg = (
                    "max_waiting_on_child_no_progress_seconds must be <="
                    " max_waiting_on_child_seconds"
                )
                raise ValueError(msg)

    def _validate_post_tool_result_progression(self) -> None:
        if self.post_tool_result_progression_seconds is None:
            return
        if not self.post_tool_result_progression_seconds > 0:
            msg = "post_tool_result_progression_seconds must be positive when set"
            raise ValueError(msg)

    def _validate_repeated_error_fields(self) -> None:
        if (
            self.repeated_error_consecutive_threshold is not None
            and self.repeated_error_consecutive_threshold <= 0
        ):
            msg = "repeated_error_consecutive_threshold must be positive when set"
            raise ValueError(msg)
        if (
            self.repeated_error_window_count is not None
            and self.repeated_error_window_count <= 0
        ):
            msg = "repeated_error_window_count must be positive when set"
            raise ValueError(msg)
        if (
            self.repeated_error_window_seconds is not None
            and self.repeated_error_window_seconds <= 0
        ):
            msg = "repeated_error_window_seconds must be positive when set"
            raise ValueError(msg)
