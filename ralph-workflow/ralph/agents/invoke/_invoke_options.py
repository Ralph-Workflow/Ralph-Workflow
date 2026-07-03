from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

_INVOKE_OPTS_UNSET: object = object()

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.agents.idle_watchdog import SubagentPidRegistry, WaitingStatusListener
    from ralph.agents.invoke._workspace import WorkspaceMonitor
    from ralph.agents.invoke._workspace_change_classifier import WorkspaceChangeClassifier
    from ralph.phases.required_artifacts import RequiredArtifact
    from ralph.process.monitor import SubagentPidSource


@dataclass(frozen=True)
class InvokeOptions:
    """Options for agent invocation."""

    # Optional workspace-monitor factory. Production callers leave this
    # ``None`` so :mod:`ralph.agents.invoke` constructs the real
    # ``WorkspaceMonitor`` (which starts a real watchdog observer).
    # Tests that only exercise routing / dispatch behavior inject a
    # factory that returns ``None`` (skip the observer entirely) or a
    # fake monitor so they do not block on the real watchdog observer's
    # ``start`` / ``stop`` cost under the 1-second per-test timeout
    # enforced by ``tests/conftest.py``. The factory signature mirrors
    # :func:`ralph.agents.invoke._start_workspace_monitor` so callers
    # can pass a thin ``lambda *args, **kwargs: None`` shortcut.
    workspace_monitor_factory: Callable[
        [Path, WorkspaceChangeClassifier | None], WorkspaceMonitor | None
    ] | None = None

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
    no_progress_quiet_seconds: float | None = None
    no_progress_quiet_minimum_invocation_seconds: float | None = None
    no_progress_quiet_heartbeat_ceiling_seconds: float | None | object = _INVOKE_OPTS_UNSET
    post_tool_result_progression_seconds: float | None = None
    repeated_error_consecutive_threshold: int | None = None
    repeated_error_window_count: int | None = None
    repeated_error_window_seconds: float | None = None
    activity_evidence_ttl_seconds: float | None = None
    workspace_change_weights: dict[str, float] | None = None
    process_monitor_enabled: bool | None = None
    subagent_output_capture_enabled: bool | None = None
    subagent_output_poll_interval_seconds: float | None = None
    os_descendant_only_ceiling_seconds: float | None | object = _INVOKE_OPTS_UNSET
    os_descendant_only_suspect_seconds: float | None | object = _INVOKE_OPTS_UNSET
    cpu_idle_seconds: float | None | object = _INVOKE_OPTS_UNSET
    log_growth_seconds: float | None | object = _INVOKE_OPTS_UNSET
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
    # Live runtime signals from the pipeline that the watchdog consults
    # on every evaluate() call so the StuckClassifier gate can return
    # DUPLICATE_KILL (when the pipeline is already in a wait state)
    # or WAITING_ON_CONNECTIVITY (when the network is offline) and
    # defer the fire. Both providers are optional; the watchdog
    # falls back to "no live signal" when they are None.
    connectivity_state_provider: Callable[[], str | None] | None = None
    is_waiting_state_provider: Callable[[], bool] | None = None
    # R1 / R5 (Trustworthy Idle Watchdog spec): when the orchestrator
    # pre-builds a per-invocation ``SubagentPidRegistry`` and its
    # per-transport ``SubagentPidSource``, it threads them through
    # here so the SAME registry reaches both the strategy layer
    # (``strategy_for_command(..., subagent_pid_source=...)``) and
    # the parser layer (``stream_parsed_agent_activity(...
    # subagent_pid_registry=...)``). Without these fields, the
    # strategy layer builds a FRESH registry internally and the
    # parser-registered PIDs never reach the strategy's filtered
    # count -- the watchdog-visible filtered subagent count is
    # desynchronized from the parser's authoritative registration
    # set. The shared-registry contract is documented at
    # ``ralph/agents/registry.py:build_subagent_pid_registry``.
    #
    # Both fields default to ``None`` so the legacy direct-call
    # signature ``invoke_agent(config, prompt_file, options=...)``
    # still works (the strategy layer builds a fresh registry
    # internally for backward compat with test fakes that pre-date
    # the R5 cross-transport wiring).
    subagent_pid_registry: SubagentPidRegistry | None = None
    subagent_pid_source: SubagentPidSource | None = None
