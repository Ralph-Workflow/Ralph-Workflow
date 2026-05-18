"""Dataclass types for agent invocation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.agents.completion_signals import CompletionSignals
    from ralph.agents.execution_state import GenericExecutionStrategy, OpenCodeExecutionStrategy
    from ralph.agents.idle_watchdog import TimeoutPolicy, WaitingStatusListener
    from ralph.agents.invoke._workspace import WorkspaceMonitor
    from ralph.agents.timeout_clock import Clock
    from ralph.config.models import AgentConfig
    from ralph.phases.required_artifacts import RequiredArtifact
    from ralph.process.liveness import LivenessProbe
if TYPE_CHECKING:
    class _EvalCompletionFn(Protocol):
        def __call__(
            self,
            workspace: Path,
            raw_output: list[str] | None = None,
            *,
            required_artifact: RequiredArtifact | None = None,
        ) -> CompletionSignals: ...


@dataclass(frozen=True)
class _ProcessReaderCtx:

    @dataclass(frozen=True)
    class InvokeOptions:
        """Options for agent invocation.

        Attributes:
            model_flag: Optional model override flag string.
            session_id: Optional session identifier for resume-capable agents.
            verbose: Whether to pass verbose flag to agent.
            show_progress: Whether to show tqdm progress bar.
            workspace_path: Optional path to workspace for file-change monitoring.
            extra_env: Optional environment overrides for the subprocess.
            idle_timeout_seconds: Optional maximum idle time without agent output.
            drain_window_seconds: Optional drain window duration in seconds.
            max_waiting_on_child_seconds: Optional ceiling on cumulative WAITING_ON_CHILD time.
            idle_poll_interval_seconds: Optional poll interval for the read loop.
            parent_exit_grace_seconds: Optional grace window after parent exit.
            descendant_wait_timeout_seconds: Optional ceiling for descendant-wait.
            process_exit_wait_seconds: Optional timeout for post-EOF subprocess exit.
            max_session_seconds: Optional absolute session wall-clock ceiling.
            waiting_status_interval_seconds: Optional periodic status emission cadence while
                WAITING_ON_CHILD. Does NOT affect timeout safety or ceiling math.
            suspect_waiting_on_child_seconds: Optional suspicion threshold in seconds;
                emits a warning event but does NOT shorten the hard-stop ceiling.
            child_progress_ttl_seconds: Maximum seconds since last child progress signal
                before the child is treated as not-progressing.
            child_heartbeat_ttl_seconds: Maximum seconds since last child heartbeat before
                heartbeat is considered stale.
            child_stale_label_ttl_seconds: Grace period during which a child label may
                persist after the underlying child evidence has gone stale.
            child_exit_reconcile_seconds: Reconciliation window after stdout EOF during
                which late terminal acks are still accepted.
        """

        model_flag: str | None = None
        session_id: str | None = None
        verbose: bool = False
        show_progress: bool = True
        workspace_path: Path | None = None
        extra_env: dict[str, str] | None = None
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
        pure: bool = False
        system_prompt_file: str | None = None
        waiting_listener: WaitingStatusListener | None = None
        permission_prompt_listener: Callable[[str], None] | None = None
        required_artifact: RequiredArtifact | None = None
        explicit_completion_seen: bool = False
        captured_session_id: str | None = None
        initial_session_id: str | None = None
        settings_json: str | None = None
        stop_sentinel_path: Path | None = None

    @dataclass(frozen=True)
    class _BuildCommandOptions:
        model_flag: str | None = None
        session_id: str | None = None
        verbose: bool = False
        pure: bool = False
        mcp_endpoint: str | None = None
        allowed_mcp_tool_names: tuple[str, ...] = ()
        system_prompt_file: str | None = None
        workspace_path: Path | None = None
        initial_session_id: str | None = None
        settings_json: str | None = None
        stop_sentinel_path: Path | None = None

    @dataclass(frozen=True)
    class _ChoiceMenuOption:
        index: int
        label: str
        selected: bool

    @dataclass(frozen=True)
    class _ChoiceMenuState:
        prompt: str
        options: tuple[_ChoiceMenuOption, ...]
        selected_index: int | None
        confirm_footer: str

    @dataclass(frozen=True)
    class ResolvedInvocationRuntime:
        """Resolved runtime configuration for a single agent invocation.

        ``agent_env`` is the environment passed to the agent subprocess.
        ``server_env`` holds extra variables forwarded to the MCP server process.
        ``mcp_endpoint`` is the endpoint URL when MCP transport is used.
        """

        agent_env: dict[str, str] | None = None
        server_env: dict[str, str] | None = None
        mcp_endpoint: str | None = None

    @dataclass(frozen=True)
    class _AgentRunCtx:
        config: AgentConfig
        show_progress: bool
        extra_env: dict[str, str] | None
        workspace_path: Path | None
        policy: TimeoutPolicy
        execution_strategy: GenericExecutionStrategy | OpenCodeExecutionStrategy | None = None
        liveness_probe: LivenessProbe | None = None
        waiting_listener: WaitingStatusListener | None = None
        monitor: WorkspaceMonitor | None = None
        required_artifact: RequiredArtifact | None = None
        clock: Clock | None = None
        evaluate_completion_fn: _EvalCompletionFn | None = None

    @dataclass(frozen=True)
    class _PtyExtras:
        expected_session_id: str | None = None
        stop_sentinel_path: Path | None = None
        permission_prompt_listener: Callable[[str], None] | None = None

    policy: TimeoutPolicy
    execution_strategy: GenericExecutionStrategy | OpenCodeExecutionStrategy | None = None
    liveness_probe: LivenessProbe | None = None
    waiting_listener: WaitingStatusListener | None = None
    monitor: WorkspaceMonitor | None = None


InvokeOptions = _ProcessReaderCtx.InvokeOptions
_BuildCommandOptions = _ProcessReaderCtx._BuildCommandOptions
_ChoiceMenuOption = _ProcessReaderCtx._ChoiceMenuOption
_ChoiceMenuState = _ProcessReaderCtx._ChoiceMenuState
ResolvedInvocationRuntime = _ProcessReaderCtx.ResolvedInvocationRuntime
_AgentRunCtx = _ProcessReaderCtx._AgentRunCtx
_PtyExtras = _ProcessReaderCtx._PtyExtras
