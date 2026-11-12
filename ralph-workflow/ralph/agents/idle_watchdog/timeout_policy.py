from __future__ import annotations

from dataclasses import dataclass, field

from ralph.agents.idle_watchdog._timeout_profile import TimeoutProfile
from ralph.agents.idle_watchdog._workspace_change_kind import (
    DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS,
    WorkspaceChangeKind,
)
from ralph.timeout_defaults import (
    AGENT_IDLE_ACTIVITY_EVIDENCE_TTL_SECONDS,
    CPU_IDLE_SECONDS,
    DESCENDANT_WAIT_POLL_SECONDS,
    DESCENDANT_WAIT_TIMEOUT_SECONDS,
    DRAIN_WINDOW_SECONDS,
    IDLE_POLL_INTERVAL_SECONDS,
    LOG_GROWTH_SECONDS,
    MAX_WAITING_ON_CHILD_NO_PROGRESS_SECONDS,
    MAX_WAITING_ON_CHILD_SECONDS,
    NO_OUTPUT_AT_START_SECONDS,
    NO_PROGRESS_QUIET_HEARTBEAT_CEILING_SECONDS,
    NO_PROGRESS_QUIET_MINIMUM_INVOCATION_SECONDS,
    NO_PROGRESS_QUIET_SECONDS,
    NO_PROGRESS_QUIET_STRICTLY_STUCK_SECONDS,
    OS_DESCENDANT_ONLY_CEILING_SECONDS,
    OS_DESCENDANT_ONLY_SUSPECT_SECONDS,
    PARENT_EXIT_GRACE_SECONDS,
    POST_TOOL_RESULT_PROGRESSION_SECONDS,
    PROCESS_EXIT_WAIT_SECONDS,
    PROCESS_MONITOR_ENABLED,
    REPEATED_ERROR_CONSECUTIVE_THRESHOLD,
    REPEATED_ERROR_WINDOW_COUNT,
    REPEATED_ERROR_WINDOW_SECONDS,
    SILENT_SUBAGENT_SECONDS,
    STUCK_JOB_SUB_CEILING_SECONDS,
    SUBAGENT_OUTPUT_CAPTURE_ENABLED,
    SUBAGENT_OUTPUT_POLL_INTERVAL_SECONDS,
    SUSPECT_WAITING_ON_CHILD_SECONDS,
    WAITING_STATUS_INTERVAL_SECONDS,
    WATCHDOG_LOG_THROTTLE_SECONDS,
    WATCHDOG_SUBAGENT_PROGRESS_INTERVAL_SECONDS,
)

_VALID_WORKSPACE_CHANGE_WEIGHT_KEYS: frozenset[str] = frozenset(
    {kind.value for kind in WorkspaceChangeKind}
)
_VALID_WORKSPACE_CHANGE_WEIGHT_VALUES: frozenset[float] = frozenset({0.0, 1.0})


@dataclass(frozen=True)
class TimeoutPolicy:
    idle_timeout_seconds: float | None
    profile: TimeoutProfile = TimeoutProfile.STANDARD
    activity_only_operator_cap_seconds: float | None = None
    activity_only_status_interval_seconds: float = WAITING_STATUS_INTERVAL_SECONDS
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
    no_progress_quiet_seconds: float | None = NO_PROGRESS_QUIET_SECONDS
    no_progress_quiet_minimum_invocation_seconds: float | None = (
        NO_PROGRESS_QUIET_MINIMUM_INVOCATION_SECONDS
    )
    no_output_at_start_seconds: float | None = NO_OUTPUT_AT_START_SECONDS
    post_tool_result_progression_seconds: float | None = POST_TOOL_RESULT_PROGRESSION_SECONDS
    repeated_error_consecutive_threshold: int | None = REPEATED_ERROR_CONSECUTIVE_THRESHOLD
    repeated_error_window_count: int | None = REPEATED_ERROR_WINDOW_COUNT
    repeated_error_window_seconds: float | None = REPEATED_ERROR_WINDOW_SECONDS
    activity_evidence_ttl_seconds: float | None = AGENT_IDLE_ACTIVITY_EVIDENCE_TTL_SECONDS
    silent_subagent_seconds: float | None = SILENT_SUBAGENT_SECONDS
    workspace_change_weights: dict[str, float] | None = field(
        default_factory=lambda: dict(DEFAULT_AGENT_WORKSPACE_CHANGE_WEIGHTS)
    )
    process_monitor_enabled: bool = PROCESS_MONITOR_ENABLED
    subagent_output_capture_enabled: bool = SUBAGENT_OUTPUT_CAPTURE_ENABLED
    subagent_output_poll_interval_seconds: float = SUBAGENT_OUTPUT_POLL_INTERVAL_SECONDS
    os_descendant_only_ceiling_seconds: float | None = OS_DESCENDANT_ONLY_CEILING_SECONDS
    os_descendant_only_suspect_seconds: float | None = OS_DESCENDANT_ONLY_SUSPECT_SECONDS
    cpu_idle_seconds: float | None = CPU_IDLE_SECONDS
    log_growth_seconds: float | None = LOG_GROWTH_SECONDS
    no_progress_quiet_strictly_stuck_seconds: float | None = (
        NO_PROGRESS_QUIET_STRICTLY_STUCK_SECONDS
    )
    no_progress_quiet_heartbeat_ceiling_seconds: float | None = (
        NO_PROGRESS_QUIET_HEARTBEAT_CEILING_SECONDS
    )
    watchdog_log_throttle_seconds: float = WATCHDOG_LOG_THROTTLE_SECONDS
    watchdog_subagent_progress_interval_seconds: float = WATCHDOG_SUBAGENT_PROGRESS_INTERVAL_SECONDS

    stuck_job_sub_ceiling_seconds: float | None = STUCK_JOB_SUB_CEILING_SECONDS

    def __post_init__(self) -> None:
        self._validate_profile()
        self._validate_idle_fields()
        self._validate_session_and_poll_fields()
        self._validate_waiting_status_fields()
        self._validate_no_progress_quiet_seconds()
        self._validate_no_progress_quiet_minimum_invocation_seconds()
        self._validate_no_output_at_start_seconds()
        self._validate_post_tool_result_progression()
        self._validate_repeated_error_fields()
        self._validate_activity_evidence_ttl()
        self._validate_silent_subagent_seconds()
        self._validate_workspace_change_weights()
        self._validate_subagent_output_poll_interval()
        self._validate_os_descendant_only_fields()
        self._validate_strictly_stuck_seconds()
        self._validate_no_progress_quiet_heartbeat_ceiling_seconds()
        self._validate_watchdog_log_throttle_seconds()
        self._validate_watchdog_subagent_progress_interval_seconds()
        self._validate_stuck_job_sub_ceiling_seconds()

    def _validate_profile(self) -> None:
        if self.profile == TimeoutProfile.ACTIVITY_ONLY and self.idle_timeout_seconds is None:
            msg = "activity_only profile requires idle_timeout_seconds"
            raise ValueError(msg)
        if (
            self.activity_only_operator_cap_seconds is not None
            and self.activity_only_operator_cap_seconds <= 0.0
        ):
            msg = "activity_only_operator_cap_seconds must be positive when set"
            raise ValueError(msg)
        if self.activity_only_status_interval_seconds <= 0.0:
            msg = "activity_only_status_interval_seconds must be positive"
            raise ValueError(msg)

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

    def _validate_no_progress_quiet_seconds(self) -> None:
        if self.no_progress_quiet_seconds is None:
            return
        if self.no_progress_quiet_seconds <= 0:
            msg = "no_progress_quiet_seconds must be positive when set"
            raise ValueError(msg)
        limit = self.max_waiting_on_child_no_progress_seconds
        if limit is not None and self.no_progress_quiet_seconds > limit:
            msg = "no_progress_quiet_seconds must be <= max_waiting_on_child_no_progress_seconds"
            raise ValueError(msg)

    def _validate_no_progress_quiet_minimum_invocation_seconds(self) -> None:
        if self.no_progress_quiet_minimum_invocation_seconds is None:
            return
        if self.no_progress_quiet_minimum_invocation_seconds <= 0:
            msg = "no_progress_quiet_minimum_invocation_seconds must be positive when set"
            raise ValueError(msg)
        if (
            self.no_progress_quiet_seconds is not None
            and self.no_progress_quiet_minimum_invocation_seconds > self.no_progress_quiet_seconds
        ):
            msg = (
                "no_progress_quiet_minimum_invocation_seconds must be <= no_progress_quiet_seconds"
            )
            raise ValueError(msg)

    def _validate_no_output_at_start_seconds(self) -> None:
        if self.no_output_at_start_seconds is None:
            return
        if self.no_output_at_start_seconds <= 0:
            msg = "no_output_at_start_seconds must be positive when set"
            raise ValueError(msg)
        limit = self.max_waiting_on_child_no_progress_seconds
        if limit is not None and self.no_output_at_start_seconds >= limit:
            if self.no_output_at_start_seconds == NO_OUTPUT_AT_START_SECONDS:
                object.__setattr__(self, "no_output_at_start_seconds", None)
                return
            msg = (
                "no_output_at_start_seconds must be strictly less than "
                "max_waiting_on_child_no_progress_seconds"
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
        if self.repeated_error_window_count is not None and self.repeated_error_window_count <= 0:
            msg = "repeated_error_window_count must be positive when set"
            raise ValueError(msg)
        if (
            self.repeated_error_window_seconds is not None
            and self.repeated_error_window_seconds <= 0
        ):
            msg = "repeated_error_window_seconds must be positive when set"
            raise ValueError(msg)

    def _validate_activity_evidence_ttl(self) -> None:
        if self.activity_evidence_ttl_seconds is None:
            return
        if self.activity_evidence_ttl_seconds < 0:
            msg = "activity_evidence_ttl_seconds must be >= 0 when set"
            raise ValueError(msg)

    def _validate_silent_subagent_seconds(self) -> None:
        if self.silent_subagent_seconds is None:
            return
        if self.silent_subagent_seconds <= 0:
            msg = "silent_subagent_seconds must be > 0 when set"
            raise ValueError(msg)

    def _validate_workspace_change_weights(self) -> None:
        if self.workspace_change_weights is None:
            return
        for key, value in self.workspace_change_weights.items():
            if key not in _VALID_WORKSPACE_CHANGE_WEIGHT_KEYS:
                msg = (
                    f"workspace_change_weights[{key!r}] is not a valid"
                    f" WorkspaceChangeKind value; allowed:"
                    f" {sorted(_VALID_WORKSPACE_CHANGE_WEIGHT_KEYS)}"
                )
                raise ValueError(msg)
            if value not in _VALID_WORKSPACE_CHANGE_WEIGHT_VALUES:
                msg = (
                    f"workspace_change_weights[{key!r}]={value!r}"
                    f" is not a binary weight; allowed: {{0.0, 1.0}}"
                )
                raise ValueError(msg)

    def _validate_subagent_output_poll_interval(self) -> None:
        if self.subagent_output_poll_interval_seconds <= 0:
            msg = "subagent_output_poll_interval_seconds must be positive"
            raise ValueError(msg)

    def _validate_os_descendant_only_fields(self) -> None:
        if (
            self.os_descendant_only_ceiling_seconds is not None
            and self.os_descendant_only_ceiling_seconds <= 0
        ):
            msg = "os_descendant_only_ceiling_seconds must be positive when set"
            raise ValueError(msg)

        if (
            self.os_descendant_only_ceiling_seconds is not None
            and self.os_descendant_only_ceiling_seconds > self.max_waiting_on_child_seconds
        ):
            msg = "os_descendant_only_ceiling_seconds must be <= max_waiting_on_child_seconds"
            raise ValueError(msg)

        if (
            self.os_descendant_only_ceiling_seconds is not None
            and self.max_waiting_on_child_no_progress_seconds is not None
            and self.os_descendant_only_ceiling_seconds
            > self.max_waiting_on_child_no_progress_seconds
        ):
            msg = (
                "os_descendant_only_ceiling_seconds must be <="
                " max_waiting_on_child_no_progress_seconds"
            )
            raise ValueError(msg)
        if self.os_descendant_only_suspect_seconds is not None:
            if self.os_descendant_only_suspect_seconds <= 0:
                msg = "os_descendant_only_suspect_seconds must be positive when set"
                raise ValueError(msg)
            if (
                self.os_descendant_only_ceiling_seconds is not None
                and self.os_descendant_only_suspect_seconds
                >= self.os_descendant_only_ceiling_seconds
            ):
                msg = (
                    "os_descendant_only_suspect_seconds must be <"
                    " os_descendant_only_ceiling_seconds"
                )
                raise ValueError(msg)
            if self.suspect_waiting_on_child_seconds is not None and (
                self.os_descendant_only_suspect_seconds >= self.max_waiting_on_child_seconds
            ):
                msg = "os_descendant_only_suspect_seconds must be < max_waiting_on_child_seconds"
                raise ValueError(msg)

    def _validate_strictly_stuck_seconds(self) -> None:
        if self.no_progress_quiet_strictly_stuck_seconds is None:
            return
        if self.no_progress_quiet_strictly_stuck_seconds <= 0:
            msg = "no_progress_quiet_strictly_stuck_seconds must be positive when set"
            raise ValueError(msg)
        if self.no_progress_quiet_strictly_stuck_seconds > self.max_waiting_on_child_seconds:
            msg = "no_progress_quiet_strictly_stuck_seconds must be <= max_waiting_on_child_seconds"
            raise ValueError(msg)

    def _validate_no_progress_quiet_heartbeat_ceiling_seconds(self) -> None:
        if self.no_progress_quiet_heartbeat_ceiling_seconds is None:
            return
        if self.no_progress_quiet_heartbeat_ceiling_seconds <= 0:
            msg = "no_progress_quiet_heartbeat_ceiling_seconds must be positive when set"
            raise ValueError(msg)
        if (
            self.no_progress_quiet_seconds is not None
            and self.no_progress_quiet_heartbeat_ceiling_seconds > self.no_progress_quiet_seconds
        ):
            msg = (
                "no_progress_quiet_heartbeat_ceiling_seconds must be <="
                " no_progress_quiet_seconds when both are set"
            )
            raise ValueError(msg)

    def _validate_watchdog_log_throttle_seconds(self) -> None:
        if self.watchdog_log_throttle_seconds <= 0:
            msg = "watchdog_log_throttle_seconds must be positive"
            raise ValueError(msg)

    def _validate_watchdog_subagent_progress_interval_seconds(self) -> None:
        if self.watchdog_subagent_progress_interval_seconds <= 0:
            msg = "watchdog_subagent_progress_interval_seconds must be positive"
            raise ValueError(msg)

    def _validate_stuck_job_sub_ceiling_seconds(self) -> None:
        if self.stuck_job_sub_ceiling_seconds is None:
            return
        if self.stuck_job_sub_ceiling_seconds <= 0:
            msg = "stuck_job_sub_ceiling_seconds must be positive when set"
            raise ValueError(msg)
        if self.stuck_job_sub_ceiling_seconds > self.max_waiting_on_child_seconds:
            msg = "stuck_job_sub_ceiling_seconds must be <= max_waiting_on_child_seconds"
            raise ValueError(msg)
