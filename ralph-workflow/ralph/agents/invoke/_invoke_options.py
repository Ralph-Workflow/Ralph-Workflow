from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.agents.idle_watchdog import WaitingStatusListener
    from ralph.phases.required_artifacts import RequiredArtifact


@dataclass(frozen=True)
class InvokeOptions:
    """Options for agent invocation."""

    model_flag: str | None = None
    session_id: str | None = None
    verbose: bool = False
    show_progress: bool = True
    workspace_path: Path | None = None
    extra_env: dict[str, str] | None = None
    unsafe_mode: bool = False
    idle_timeout_seconds: float | None = None
    drain_window_seconds: float | None = None
    max_waiting_on_child_seconds: float | None = None
    idle_poll_interval_seconds: float | None = None
    parent_exit_grace_seconds: float | None = None
    descendant_wait_timeout_seconds: float | None = None
    descendant_wait_poll_seconds: float | None = None
    process_exit_wait_seconds: float | None = None
    max_session_seconds: float | None = None
    waiting_status_interval_seconds: float | None = None
    suspect_waiting_on_child_seconds: float | None = None
    child_progress_ttl_seconds: float | None = None
    child_heartbeat_ttl_seconds: float | None = None
    child_stale_label_ttl_seconds: float | None = None
    child_exit_reconcile_seconds: float | None = None
    max_waiting_on_child_no_progress_seconds: float | None = None
    post_tool_result_progression_seconds: float | None = None
    repeated_error_consecutive_threshold: int | None = None
    repeated_error_window_count: int | None = None
    repeated_error_window_seconds: float | None = None
    activity_evidence_ttl_seconds: float | None = None
    workspace_change_weights: dict[str, float] | None = None
    pure: bool = False
    system_prompt_file: str | None = None
    waiting_listener: WaitingStatusListener | None = None
    pre_output_listener: Callable[[], None] | None = None
    permission_prompt_listener: Callable[[str], None] | None = None
    required_artifact: RequiredArtifact | None = None
    explicit_completion_seen: bool = False
    captured_session_id: str | None = None
    initial_session_id: str | None = None
    settings_json: str | None = None
    stop_sentinel_path: Path | None = None
