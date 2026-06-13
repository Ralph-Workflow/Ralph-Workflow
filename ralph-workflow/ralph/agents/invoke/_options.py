"""Runtime options and policy building for agent invocation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from ralph.agents.idle_watchdog import TimeoutPolicy, WaitingStatusListener
from ralph.agents.invoke._types import InvokeOptions

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
    system_prompt_file: str | None = None
    waiting_listener: WaitingStatusListener | None = None
    pre_output_listener: Callable[[], None] | None = None
    permission_prompt_listener: Callable[[str], None] | None = None
    required_artifact: RequiredArtifact | None = None


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
        system_prompt_file=rt.system_prompt_file,
        waiting_listener=rt.waiting_listener,
        pre_output_listener=rt.pre_output_listener,
        permission_prompt_listener=rt.permission_prompt_listener,
        required_artifact=rt.required_artifact,
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
        post_tool_result_progression_seconds=general_config.agent_post_tool_result_progression_seconds,
        repeated_error_consecutive_threshold=general_config.agent_repeated_error_consecutive_threshold,
        repeated_error_window_count=general_config.agent_repeated_error_window_count,
        repeated_error_window_seconds=general_config.agent_repeated_error_window_seconds,
        activity_evidence_ttl_seconds=general_config.agent_idle_activity_evidence_ttl_seconds,
        workspace_change_weights=general_config.agent_workspace_change_weights,
        child_progress_ttl_seconds=general_config.agent_child_progress_ttl_seconds,
        child_heartbeat_ttl_seconds=general_config.agent_child_heartbeat_ttl_seconds,
        child_stale_label_ttl_seconds=general_config.agent_child_stale_label_ttl_seconds,
        child_exit_reconcile_seconds=general_config.agent_child_exit_reconcile_seconds,
    )


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
