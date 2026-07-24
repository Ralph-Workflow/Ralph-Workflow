"""Runtime options and policy building for agent invocation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.agents.idle_watchdog import TimeoutPolicy, WaitingStatusListener
from ralph.agents.invoke._invoke_options import _INVOKE_OPTS_UNSET
from ralph.agents.invoke._types import InvokeOptions
from ralph.timeout_defaults import STUCK_JOB_SUB_CEILING_SECONDS

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.agents.invoke._workspace import WorkspaceMonitor
    from ralph.config.models import GeneralConfig
    from ralph.phases.required_artifacts import RequiredArtifact


@dataclass(frozen=True)
class InvokeRuntimeOptions:
    """Non-timeout runtime options for agent invocation."""

    verbose: bool = False
    show_progress: bool = True
    workspace_path: Path | None = None
    extra_env: dict[str, str] | None = None
    pure: bool = False
    session_id: str | None = None
    master_prompt_file: str | None = None
    waiting_listener: WaitingStatusListener | None = None
    pre_output_listener: Callable[[], None] | None = None
    permission_prompt_listener: Callable[[str], None] | None = None
    required_artifact: RequiredArtifact | None = None
    requires_completion_evidence: bool = True
    # Live pipeline signals that the watchdog consults on every
    # evaluate() call so the StuckClassifier gate can return
    # DUPLICATE_KILL (when the pipeline is already in a wait state)
    # or WAITING_ON_CONNECTIVITY (when the network is offline) and
    # defer the fire. Both providers are optional; the watchdog
    # falls back to "no live signal" when they are None.
    connectivity_state_provider: Callable[[], str | None] | None = None
    is_waiting_state_provider: Callable[[], bool] | None = None


def build_invoke_options_from_config(
    general_config: GeneralConfig,
    runtime: InvokeRuntimeOptions | None = None,
) -> InvokeOptions:
    """Build InvokeOptions from GeneralConfig, mapping all timeout fields."""
    rt = runtime if runtime is not None else InvokeRuntimeOptions()
    return InvokeOptions(
        verbose=rt.verbose,
        show_progress=rt.show_progress,
        workspace_path=rt.workspace_path,
        extra_env=rt.extra_env,
        unsafe_mode=general_config.workflow.unsafe_mode,
        pure=rt.pure,
        session_id=rt.session_id,
        master_prompt_file=rt.master_prompt_file,
        waiting_listener=rt.waiting_listener,
        pre_output_listener=rt.pre_output_listener,
        permission_prompt_listener=rt.permission_prompt_listener,
        required_artifact=rt.required_artifact,
        requires_completion_evidence=rt.requires_completion_evidence,
        idle_timeout_seconds=general_config.agent_idle_timeout_seconds,
        drain_window_seconds=general_config.agent_idle_drain_window_seconds,
        max_waiting_on_child_seconds=general_config.agent_idle_max_waiting_on_child_seconds,
        idle_poll_interval_seconds=general_config.agent_idle_poll_interval_seconds,
        parent_exit_grace_seconds=general_config.agent_parent_exit_grace_seconds,
        descendant_wait_timeout_seconds=general_config.agent_descendant_wait_timeout_seconds,
        descendant_wait_poll_seconds=general_config.agent_descendant_wait_poll_seconds,
        process_exit_wait_seconds=general_config.agent_process_exit_wait_seconds,
        max_session_seconds=general_config.agent_max_session_seconds,
        waiting_status_interval_seconds=general_config.agent_waiting_status_interval_seconds,
        suspect_waiting_on_child_seconds=general_config.agent_suspect_waiting_on_child_seconds,
        max_waiting_on_child_no_progress_seconds=general_config.agent_idle_no_progress_waiting_on_child_seconds,
        no_progress_quiet_seconds=general_config.agent_no_progress_quiet_seconds,
        no_progress_quiet_minimum_invocation_seconds=general_config.agent_no_progress_quiet_minimum_invocation_seconds,
        no_progress_quiet_heartbeat_ceiling_seconds=general_config.agent_no_progress_quiet_heartbeat_ceiling_seconds,
        post_tool_result_progression_seconds=general_config.agent_post_tool_result_progression_seconds,
        repeated_error_consecutive_threshold=general_config.agent_repeated_error_consecutive_threshold,
        repeated_error_window_count=general_config.agent_repeated_error_window_count,
        repeated_error_window_seconds=general_config.agent_repeated_error_window_seconds,
        activity_evidence_ttl_seconds=general_config.agent_idle_activity_evidence_ttl_seconds,
        workspace_change_weights=general_config.agent_workspace_change_weights,
        process_monitor_enabled=general_config.agent_process_monitor_enabled,
        subagent_output_capture_enabled=general_config.agent_subagent_output_capture_enabled,
        subagent_output_poll_interval_seconds=general_config.agent_subagent_output_poll_interval_seconds,
        child_progress_ttl_seconds=general_config.agent_child_progress_ttl_seconds,
        child_heartbeat_ttl_seconds=general_config.agent_child_heartbeat_ttl_seconds,
        child_stale_label_ttl_seconds=general_config.agent_child_stale_label_ttl_seconds,
        child_exit_reconcile_seconds=general_config.agent_child_exit_reconcile_seconds,
        os_descendant_only_ceiling_seconds=general_config.agent_os_descendant_only_ceiling_seconds,
        os_descendant_only_suspect_seconds=general_config.agent_os_descendant_only_suspect_seconds,
        cpu_idle_seconds=general_config.agent_cpu_idle_seconds,
        log_growth_seconds=general_config.agent_log_growth_seconds,
        connectivity_state_provider=rt.connectivity_state_provider,
        is_waiting_state_provider=rt.is_waiting_state_provider,
    )


def _get_os_descendant_field(value: float | None | object, fallback: float | None) -> float | None:
    if value is _INVOKE_OPTS_UNSET:
        return fallback
    return cast("float | None", value)


def _policy_from_options(opts: InvokeOptions) -> TimeoutPolicy:
    """Build a TimeoutPolicy from InvokeOptions, falling back to policy defaults for None fields."""
    _base = TimeoutPolicy(idle_timeout_seconds=opts.idle_timeout_seconds)
    _effective_max = (
        opts.max_waiting_on_child_seconds
        if opts.max_waiting_on_child_seconds is not None
        else _base.max_waiting_on_child_seconds
    )
    # Prefer opts values; fall back to TimeoutPolicy defaults. Disable suspicion when
    # it would be >= the max ceiling (e.g. in tests with small max).
    _suspect = (
        opts.suspect_waiting_on_child_seconds
        if opts.suspect_waiting_on_child_seconds is not None
        else _base.suspect_waiting_on_child_seconds
    )
    if _suspect is not None and _effective_max is not None and _suspect >= _effective_max:
        _suspect = None
    _os_descendant_ceiling = _get_os_descendant_field(
        opts.os_descendant_only_ceiling_seconds, _base.os_descendant_only_ceiling_seconds
    )
    if (
        _os_descendant_ceiling is not None
        and _effective_max is not None
        and _os_descendant_ceiling > _effective_max
    ):
        _os_descendant_ceiling = None
    _os_descendant_suspect = _get_os_descendant_field(
        opts.os_descendant_only_suspect_seconds, _base.os_descendant_only_suspect_seconds
    )
    if (
        _os_descendant_suspect is not None
        and _effective_max is not None
        and (
            _os_descendant_suspect >= _effective_max
            or (
                _os_descendant_ceiling is not None
                and _os_descendant_suspect >= _os_descendant_ceiling
            )
        )
    ):
        _os_descendant_suspect = None
    return TimeoutPolicy(
        idle_timeout_seconds=opts.idle_timeout_seconds,
        drain_window_seconds=(
            opts.drain_window_seconds
            if opts.drain_window_seconds is not None
            else _base.drain_window_seconds
        ),
        max_waiting_on_child_seconds=_effective_max,
        max_session_seconds=(
            opts.max_session_seconds
            if opts.max_session_seconds is not None
            else _base.max_session_seconds
        ),
        idle_poll_interval_seconds=(
            opts.idle_poll_interval_seconds
            if opts.idle_poll_interval_seconds is not None
            else _base.idle_poll_interval_seconds
        ),
        parent_exit_grace_seconds=(
            opts.parent_exit_grace_seconds
            if opts.parent_exit_grace_seconds is not None
            else _base.parent_exit_grace_seconds
        ),
        descendant_wait_timeout_seconds=(
            opts.descendant_wait_timeout_seconds
            if opts.descendant_wait_timeout_seconds is not None
            else _base.descendant_wait_timeout_seconds
        ),
        descendant_wait_poll_seconds=(
            opts.descendant_wait_poll_seconds
            if opts.descendant_wait_poll_seconds is not None
            else _base.descendant_wait_poll_seconds
        ),
        process_exit_wait_seconds=(
            opts.process_exit_wait_seconds
            if opts.process_exit_wait_seconds is not None
            else _base.process_exit_wait_seconds
        ),
        waiting_status_interval_seconds=(
            opts.waiting_status_interval_seconds
            if opts.waiting_status_interval_seconds is not None
            else _base.waiting_status_interval_seconds
        ),
        suspect_waiting_on_child_seconds=_suspect,
        max_waiting_on_child_no_progress_seconds=(
            opts.max_waiting_on_child_no_progress_seconds
            if opts.max_waiting_on_child_no_progress_seconds is not None
            else _base.max_waiting_on_child_no_progress_seconds
            if (
                _effective_max is not None
                and _base.max_waiting_on_child_no_progress_seconds is not None
                and _base.max_waiting_on_child_no_progress_seconds <= _effective_max
            )
            else None
        ),
        post_tool_result_progression_seconds=(
            opts.post_tool_result_progression_seconds
            if opts.post_tool_result_progression_seconds is not None
            else _base.post_tool_result_progression_seconds
        ),
        no_progress_quiet_seconds=(
            opts.no_progress_quiet_seconds
            if opts.no_progress_quiet_seconds is not None
            else _base.no_progress_quiet_seconds
        ),
        no_progress_quiet_minimum_invocation_seconds=(
            opts.no_progress_quiet_minimum_invocation_seconds
            if opts.no_progress_quiet_minimum_invocation_seconds is not None
            else _base.no_progress_quiet_minimum_invocation_seconds
        ),
        no_progress_quiet_heartbeat_ceiling_seconds=_get_os_descendant_field(
            opts.no_progress_quiet_heartbeat_ceiling_seconds,
            _base.no_progress_quiet_heartbeat_ceiling_seconds,
        ),
        repeated_error_consecutive_threshold=(
            opts.repeated_error_consecutive_threshold
            if opts.repeated_error_consecutive_threshold is not None
            else _base.repeated_error_consecutive_threshold
        ),
        repeated_error_window_count=(
            opts.repeated_error_window_count
            if opts.repeated_error_window_count is not None
            else _base.repeated_error_window_count
        ),
        repeated_error_window_seconds=(
            opts.repeated_error_window_seconds
            if opts.repeated_error_window_seconds is not None
            else _base.repeated_error_window_seconds
        ),
        activity_evidence_ttl_seconds=(
            opts.activity_evidence_ttl_seconds
            if opts.activity_evidence_ttl_seconds is not None
            else _base.activity_evidence_ttl_seconds
        ),
        workspace_change_weights=(
            opts.workspace_change_weights
            if opts.workspace_change_weights is not None
            else _base.workspace_change_weights
        ),
        process_monitor_enabled=(
            opts.process_monitor_enabled
            if opts.process_monitor_enabled is not None
            else _base.process_monitor_enabled
        ),
        subagent_output_capture_enabled=(
            opts.subagent_output_capture_enabled
            if opts.subagent_output_capture_enabled is not None
            else _base.subagent_output_capture_enabled
        ),
        subagent_output_poll_interval_seconds=(
            opts.subagent_output_poll_interval_seconds
            if opts.subagent_output_poll_interval_seconds is not None
            else _base.subagent_output_poll_interval_seconds
        ),
        os_descendant_only_ceiling_seconds=_os_descendant_ceiling,
        os_descendant_only_suspect_seconds=_os_descendant_suspect,
        cpu_idle_seconds=_get_os_descendant_field(opts.cpu_idle_seconds, _base.cpu_idle_seconds),
        log_growth_seconds=_get_os_descendant_field(
            opts.log_growth_seconds, _base.log_growth_seconds
        ),
        # The stuck-job sub-ceiling must satisfy the
        # ``<= max_waiting_on_child_seconds`` validator. Disable the
        # sub-ceiling when the effective ceiling is too small to fit
        # the default sub-ceiling value (e.g. tests with a small
        # ``max_waiting_on_child_seconds``). Production callers with
        # a normal ``max_waiting_on_child_seconds`` (1800s default)
        # get the 600s sub-ceiling default.
        stuck_job_sub_ceiling_seconds=(
            STUCK_JOB_SUB_CEILING_SECONDS
            if (
                _effective_max is not None
                and _effective_max >= STUCK_JOB_SUB_CEILING_SECONDS
            )
            else None
        ),
    )


def _log_workspace_completion(monitor: WorkspaceMonitor | None) -> None:
    """Log workspace changes if monitoring.

    Args:
        monitor: Workspace monitor instance.
    """
    if monitor is None:
        return
    logger.debug(
        "Agent completed. Workspace changes: {} files, {} events",
        len(monitor.changed_files),
        monitor.event_count,
    )
