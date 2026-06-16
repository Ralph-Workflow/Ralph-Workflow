from __future__ import annotations

from dataclasses import dataclass, field

from ralph.config._general_workflow_flags import GeneralWorkflowFlags


@dataclass
class _GeneralConfigStub:
    verbosity: int = 0
    max_same_agent_retries: int = 0
    agent_idle_timeout_seconds: float | None = 300.0
    agent_idle_drain_window_seconds: float = 2.0
    agent_idle_max_waiting_on_child_seconds: float = 1800.0
    agent_idle_poll_interval_seconds: float = 0.5
    agent_max_session_seconds: float | None = None
    agent_descendant_wait_timeout_seconds: float = 30.0
    agent_descendant_wait_poll_seconds: float = 0.25
    agent_parent_exit_grace_seconds: float = 5.0
    agent_waiting_status_interval_seconds: float = 60.0
    agent_suspect_waiting_on_child_seconds: float | None = 300.0
    agent_idle_no_progress_waiting_on_child_seconds: float = 600.0
    agent_no_progress_quiet_seconds: float | None = 120.0
    agent_no_progress_quiet_minimum_invocation_seconds: float | None = 120.0
    agent_idle_activity_evidence_ttl_seconds: float | None = 30.0
    agent_post_tool_result_progression_seconds: float | None = 120.0
    agent_repeated_error_consecutive_threshold: int | None = 5
    agent_repeated_error_window_count: int | None = 8
    agent_repeated_error_window_seconds: float | None = 600.0
    agent_workspace_change_weights: dict[str, float] | None = None
    agent_child_progress_ttl_seconds: float = 300.0
    agent_child_heartbeat_ttl_seconds: float = 60.0
    agent_child_stale_label_ttl_seconds: float = 120.0
    agent_child_exit_reconcile_seconds: float = 5.0
    agent_process_exit_wait_seconds: float = 5.0
    agent_os_descendant_only_ceiling_seconds: float | None = None
    agent_os_descendant_only_suspect_seconds: float | None = None
    agent_cpu_idle_seconds: float | None = None
    agent_log_growth_seconds: float | None = None
    agent_process_monitor_enabled: bool = True
    agent_subagent_output_capture_enabled: bool = True
    agent_subagent_output_poll_interval_seconds: float = 1.0
    agent_system_prompt: str | None = None
    agent_provider: str | None = None
    verbose: bool = False
    workflow: GeneralWorkflowFlags = field(default_factory=GeneralWorkflowFlags)
