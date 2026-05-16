"""Subprocess-based agent invocation with streaming NDJSON parsing.

This module handles invoking AI agents as subprocesses, parsing their
streaming NDJSON output, and managing the lifecycle of the process.

Key features:
- Line-by-line streaming from subprocess stdout to parser
- tqdm progress bar (or rich when TTY)
- loguru structured logging for every NDJSON line
- watchdog workspace monitoring for file-change events during execution
"""

from __future__ import annotations

import codecs
import contextlib
import importlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, TYPE_CHECKING, Protocol, cast, runtime_checkable
from uuid import uuid4

from loguru import logger
from tqdm import tqdm

from ralph.agents.activity import AgentActivityKind
from ralph.agents.completion_signals import evaluate_completion
from ralph.agents.execution_state import (
    AgentExecutionState,
    GenericExecutionStrategy,
    OpenCodeExecutionStrategy,
    strategy_for_transport,
)
from ralph.agents.idle_watchdog import (
    CorroborationSnapshot,
    IdleWatchdog,
    TimeoutPolicy,
    WaitingStatusEvent,
    WaitingStatusKind,
    WaitingStatusListener,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.post_exit_watchdog import PostExitVerdict, PostExitWatchdog
from ralph.agents.timeout_clock import Clock, SystemClock
from ralph.config.enums import AgentTransport
from ralph.display.vt_normalizer import normalize_vt_text
from ralph.mcp.protocol.env import AGENT_LABEL_SCOPE_ENV, MCP_ENDPOINT_ENV
from ralph.mcp.protocol.startup import (
    PreflightError,
    ensure_no_preflight_error,
    extract_preflight_tool_names,
    initialize_request,
    initialized_notification,
    parse_http_endpoint,
    post_http_jsonrpc_with_session,
    tools_list_request,
)
from ralph.mcp.tools.names import claude_tool_name
from ralph.mcp.transport.claude import claude_mcp_config, load_existing_claude_upstream_servers
from ralph.mcp.transport.codex import prepare_codex_home_with_upstreams
from ralph.mcp.transport.common import (
    mcp_toml_as_upstreams,
    merge_mcp_toml_into_upstreams,
    set_upstream_mcp_config,
)
from ralph.mcp.transport.opencode import build_opencode_provider_config
from ralph.process.child_liveness import AliveBy, ChildLivenessRegistry, classify_child_snapshot
from ralph.process.liveness import DefaultLivenessProbe, LivenessProbe
from ralph.process.manager import (
    ManagedProcess,
    ManagedPtyProcess,
    ProcessEvent,
    ProcessStatus,
    PtySpawnOptions,
    SpawnOptions,
    get_process_manager,
)
from ralph.process.pty import read_master_chunk, wait_for_master_readable
from ralph.timeout_defaults import (
    CHILD_EXIT_RECONCILE_SECONDS,
    CHILD_HEARTBEAT_TTL_SECONDS,
    CHILD_PROGRESS_TTL_SECONDS,
    CHILD_STALE_LABEL_TTL_SECONDS,
)

_MODELED_FLAG_PARTS = 2

_NON_MEANINGFUL_ACTIVITY_KINDS: frozenset[AgentActivityKind] = frozenset(
    {AgentActivityKind.LIFECYCLE}
)
_TERMINAL_PROCESS_STATUSES: frozenset[ProcessStatus] = frozenset(
    {ProcessStatus.EXITED, ProcessStatus.KILLED, ProcessStatus.FAILED}
)

_MAX_PARSED_OUTPUT_LINES = 256
_MAX_WORKSPACE_CHANGED_FILES = 512
_EXPLICIT_COMPLETION_MARKER = "Task declared complete:"
_TURN_BOUNDARY_MARKER = "[claude turn boundary]"
_SESSION_ID_PATTERNS = (
    re.compile(r"session\s+id\s*[:=]\s*([A-Za-z0-9._:-]+)", re.IGNORECASE),
    re.compile(r"--resume\s+([A-Za-z0-9._:-]+)"),
)
_PERMISSION_PROMPT_PATTERNS = (
    re.compile(r"claude requested permissions?", re.IGNORECASE),
    re.compile(r"\bapprove\b", re.IGNORECASE),
    re.compile(r"\ballow\?", re.IGNORECASE),
    re.compile(r"enable auto mode\?", re.IGNORECASE),
    re.compile(r"enter to confirm", re.IGNORECASE),
)
_CHOICE_MENU_OPTION_RE = re.compile(r"^(?P<prefix>\u276f\s*)?(?P<index>\d+)\.\s+(?P<label>.+)$")
_MENU_QUIESCENCE_SECONDS = 0.75


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


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

    from ralph.config.models import AgentConfig, GeneralConfig
    from ralph.phases.required_artifacts import RequiredArtifact


class _HasStop(Protocol):
    """Protocol for watchdog Observer-like objects that have a stop method."""

    def stop(self) -> None: ...
    def join(self, _timeout: float | None = None) -> None: ...


@runtime_checkable
class _HasSrcPath(Protocol):
    """Protocol for watchdog events that expose a source path."""

    src_path: str


class _ObserverProtocol(_HasStop, Protocol):
    """Protocol for watchdog Observer-like objects used by this module."""

    def schedule(self, _event_handler: object, path: str, **_kwargs: object) -> None: ...
    def start(self) -> None: ...


class _WatchdogObserversModule(Protocol):
    """Typed accessor for the optional watchdog.observers module."""

    Observer: type[_ObserverProtocol]


def _create_watchdog_observer() -> _ObserverProtocol | None:
    """Construct a watchdog observer when the optional dependency is installed."""
    try:
        observers_module = cast(
            "_WatchdogObserversModule",
            importlib.import_module("watchdog.observers"),
        )
    except ImportError:
        return None
    return observers_module.Observer()


class _IdleStreamTimeoutError(RuntimeError):
    """Raised when an agent process stops producing output for too long."""

    def __init__(
        self,
        timeout_seconds: float,
        reason: WatchdogFireReason,
        *,
        diagnostic: dict[str, str | int | float | bool] | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.reason = reason
        self.diagnostic = diagnostic
        if reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG:
            duration = f"{timeout_seconds:.0f}s"
            base_msg = f"Agent kept child agents alive without producing output for {duration}"
            if diagnostic:
                cum = diagnostic.get("cumulative", "?")
                scoped = diagnostic.get("scoped_child_active", "?")
                oldest = diagnostic.get("oldest_child_seconds", "?")
                ws_delta = diagnostic.get("workspace_event_delta", "?")
                lo = diagnostic.get("lifecycle_only_activity", "?")
                msg = (
                    f"{base_msg} (cumulative={cum}s, scoped_child_active={scoped},"
                    f" oldest_child_seconds={oldest}s, workspace_event_delta={ws_delta},"
                    f" lifecycle_only_activity={lo})"
                )
            else:
                msg = base_msg
        elif reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED:
            duration = f"{timeout_seconds:.0f}s"
            msg = f"Agent exceeded max session wall-clock of {duration}"
        elif reason == WatchdogFireReason.PROCESS_EXIT_HANG:
            duration = f"{timeout_seconds:.0f}s"
            msg = f"Agent subprocess closed stdout but did not exit within {duration}"
        else:
            msg = f"Agent produced no output for {timeout_seconds:.0f}s"
        super().__init__(msg)


class AgentInvocationError(Exception):
    """Raised when agent invocation fails.

    Attributes:
        agent_name: Name of the agent that failed.
        returncode: Process exit code.
        stderr: Standard error output.
    """

    def __init__(
        self,
        agent_name: str,
        returncode: int,
        stderr: str = "",
        parsed_output: list[str] | None = None,
    ) -> None:
        """Initialize invocation error.

        Args:
            agent_name: Name of the agent.
            returncode: Process exit code.
            stderr: Standard error output.
        """
        self.agent_name = agent_name
        self.returncode = returncode
        self.stderr = stderr
        self.parsed_output = list(parsed_output) if parsed_output is not None else []
        detail = self._detail_message()
        suffix = f": {detail}" if detail else ""
        super().__init__(f"Agent '{agent_name}' failed with code {returncode}{suffix}")

    def _detail_message(self) -> str:
        stderr = self.stderr.strip()
        if stderr:
            return stderr
        if self.parsed_output:
            return " | ".join(self.parsed_output)
        return ""


class InteractivePermissionPromptError(AgentInvocationError):
    """Raised when interactive Claude reaches a permission prompt in unattended mode."""

    def __init__(self, agent_name: str, parsed_output: list[str]) -> None:
        super().__init__(
            agent_name,
            -1,
            "Interactive Claude reached a permission prompt in unattended mode",
            parsed_output,
        )


@dataclass(frozen=True)
class InactivityTimeoutOpts:
    """Optional parameters for AgentInactivityTimeoutError."""

    reason: WatchdogFireReason | None = None
    session_resume_safe: bool = False
    diagnostic: dict[str, str | int | float | bool] | None = None


class AgentInactivityTimeoutError(AgentInvocationError):
    """Raised when an agent stalls without producing output."""

    def __init__(
        self,
        agent_name: str,
        timeout_seconds: float,
        parsed_output: list[str] | None = None,
        opts: InactivityTimeoutOpts | None = None,
    ) -> None:
        _opts = opts or InactivityTimeoutOpts()
        self.timeout_seconds = timeout_seconds
        self.reason = _opts.reason
        self.session_resume_safe = _opts.session_resume_safe
        if _opts.reason == WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG:
            duration = f"{timeout_seconds:.0f}s"
            base_msg = f"Agent kept child agents alive without producing output for {duration}"
            if _opts.diagnostic:
                cum = _opts.diagnostic.get("cumulative", "?")
                scoped = _opts.diagnostic.get("scoped_child_active", "?")
                oldest = _opts.diagnostic.get("oldest_child_seconds", "?")
                ws_delta = _opts.diagnostic.get("workspace_event_delta", "?")
                lo = _opts.diagnostic.get("lifecycle_only_activity", "?")
                stderr_msg = (
                    f"{base_msg} (cumulative={cum}s, scoped_child_active={scoped},"
                    f" oldest_child_seconds={oldest}s, workspace_event_delta={ws_delta},"
                    f" lifecycle_only_activity={lo})"
                )
            else:
                stderr_msg = base_msg
        elif _opts.reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED:
            duration = f"{timeout_seconds:.0f}s"
            stderr_msg = f"Agent exceeded max session wall-clock of {duration}"
        elif _opts.reason == WatchdogFireReason.PROCESS_EXIT_HANG:
            duration = f"{timeout_seconds:.0f}s"
            stderr_msg = f"Agent subprocess closed stdout but did not exit within {duration}"
        else:
            stderr_msg = f"Agent produced no output for {timeout_seconds:.0f}s"
        super().__init__(
            agent_name,
            -1,
            stderr_msg,
            list(parsed_output) if parsed_output is not None else [],
        )


class OpenCodeResumableExitError(AgentInvocationError):
    """Raised when an agent session exits with code 0 without required
    completion evidence.

    The session can be continued; the runner maps this into a session-preserving retry.
    """

    def __init__(self, agent_name: str, session_id: str | None = None) -> None:
        self.resumable_session_id = session_id
        super().__init__(
            agent_name,
            0,
            (
                "agent session exited without required completion evidence "
                "(no artifact, no declare_complete)"
            ),
        )


class UnsupportedMcpTransportError(RuntimeError):
    """Raised when MCP-backed execution is requested for an unsupported transport."""


def _extract_session_id_from_line(line: str) -> str | None:
    try:
        parsed = cast("object", json.loads(line))
    except json.JSONDecodeError:
        stripped = line.strip()
        for pattern in _SESSION_ID_PATTERNS:
            match = pattern.search(stripped)
            if match is not None:
                return match.group(1)
        return None
    return _find_session_id(parsed)


def extract_session_id(raw_output: list[str] | tuple[str, ...]) -> str | None:
    """Extract a nested session identifier from raw NDJSON output lines."""
    for line in raw_output:
        session_id = _extract_session_id_from_line(line)
        if session_id:
            return session_id
    return None


def _bounded_output_lines(
    raw_output: list[str] | tuple[str, ...],
    *,
    explicit_completion_seen: bool = False,
) -> list[str]:
    lines = list(raw_output)
    if explicit_completion_seen and not any(_EXPLICIT_COMPLETION_MARKER in line for line in lines):
        lines.append(_EXPLICIT_COMPLETION_MARKER)
    return lines


def _find_session_id(value: object) -> str | None:
    if isinstance(value, dict):
        for key in ("session_id", "sessionId"):
            session_id = value.get(key)
            if isinstance(session_id, str) and session_id:
                return session_id
        for nested in value.values():
            session_id = _find_session_id(nested)
            if session_id:
                return session_id
    if isinstance(value, list):
        for item in value:
            session_id = _find_session_id(item)
            if session_id:
                return session_id
    return None


class WorkspaceMonitor:
    """Monitors workspace directory for file changes during agent execution.

    This allows the pipeline to detect when an agent has completed significant
    work by watching for file modifications in the workspace.
    """

    def __init__(self, workspace_path: Path) -> None:
        """Initialize workspace monitor.

        Args:
            workspace_path: Path to the workspace directory to monitor.
        """
        self._workspace = workspace_path
        self._observer: _HasStop | None = None
        self._event_count = 0
        self._seen_files: dict[str, None] = {}

    def start(self) -> None:
        """Start monitoring the workspace for file changes."""
        observer = _create_watchdog_observer()
        if observer is None:
            return

        class ChangeTracker:
            def __init__(self, monitor: WorkspaceMonitor) -> None:
                self._monitor = monitor

            def dispatch(self, event: object) -> None:
                self.on_any_event(event)

            def on_any_event(self, event: object) -> None:
                if isinstance(event, _HasSrcPath):
                    self._monitor.record_event(event.src_path)

        handler = ChangeTracker(self)
        self._observer = observer
        self._observer.schedule(handler, str(self._workspace), recursive=True)
        self._observer.start()
        logger.debug("Started workspace monitoring: {}", self._workspace)

    def record_event(self, src_path: str) -> None:
        """Record a file change event.

        Args:
            src_path: Path to the changed file.
        """
        self._seen_files.pop(src_path, None)
        self._seen_files[src_path] = None
        while len(self._seen_files) > _MAX_WORKSPACE_CHANGED_FILES:
            oldest = next(iter(self._seen_files))
            del self._seen_files[oldest]
        self._event_count += 1

    def stop(self) -> None:
        """Stop monitoring the workspace."""
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(5)
            self._observer = None
            logger.debug(
                "Stopped workspace monitoring: {} ({} events)",
                self._workspace,
                self._event_count,
            )

    @property
    def event_count(self) -> int:
        """Number of file change events detected."""
        return self._event_count

    @property
    def changed_files(self) -> set[str]:
        """Set of file paths that changed during monitoring."""
        return set(self._seen_files)


def _shell_single_quote(value: str) -> str:
    escaped = value.replace("'", "'\\''")
    return f"'{escaped}'"


def _interactive_stop_sentinel_path(session_id: str) -> Path:
    return Path(tempfile.gettempdir()) / f"ralph-claude-interactive-{session_id}.done"


def _interactive_stop_hook_settings(sentinel_path: Path) -> str:
    command = f"touch {_shell_single_quote(str(sentinel_path))}"
    settings: dict[str, object] = {
        "hooks": {
            "Stop": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": command,
                        }
                    ]
                }
            ]
        }
    }
    return json.dumps(settings)


def _prepare_interactive_claude_options(opts: InvokeOptions, config: AgentConfig) -> InvokeOptions:
    if _agent_transport(config) != AgentTransport.CLAUDE_INTERACTIVE:
        return opts
    session_id = opts.session_id or opts.initial_session_id or str(uuid4())
    sentinel_path = opts.stop_sentinel_path or _interactive_stop_sentinel_path(session_id)
    settings_json = opts.settings_json or _interactive_stop_hook_settings(sentinel_path)
    return InvokeOptions(
        model_flag=opts.model_flag,
        session_id=opts.session_id,
        verbose=opts.verbose,
        show_progress=opts.show_progress,
        workspace_path=opts.workspace_path,
        extra_env=opts.extra_env,
        idle_timeout_seconds=opts.idle_timeout_seconds,
        drain_window_seconds=opts.drain_window_seconds,
        max_waiting_on_child_seconds=opts.max_waiting_on_child_seconds,
        idle_poll_interval_seconds=opts.idle_poll_interval_seconds,
        parent_exit_grace_seconds=opts.parent_exit_grace_seconds,
        descendant_wait_timeout_seconds=opts.descendant_wait_timeout_seconds,
        descendant_wait_poll_seconds=opts.descendant_wait_poll_seconds,
        process_exit_wait_seconds=opts.process_exit_wait_seconds,
        max_session_seconds=opts.max_session_seconds,
        waiting_status_interval_seconds=opts.waiting_status_interval_seconds,
        suspect_waiting_on_child_seconds=opts.suspect_waiting_on_child_seconds,
        child_progress_ttl_seconds=opts.child_progress_ttl_seconds,
        child_heartbeat_ttl_seconds=opts.child_heartbeat_ttl_seconds,
        child_stale_label_ttl_seconds=opts.child_stale_label_ttl_seconds,
        child_exit_reconcile_seconds=opts.child_exit_reconcile_seconds,
        max_waiting_on_child_no_progress_seconds=opts.max_waiting_on_child_no_progress_seconds,
        pure=opts.pure,
        system_prompt_file=opts.system_prompt_file,
        waiting_listener=opts.waiting_listener,
        required_artifact=opts.required_artifact,
        explicit_completion_seen=opts.explicit_completion_seen,
        captured_session_id=opts.captured_session_id,
        initial_session_id=session_id,
        settings_json=settings_json,
        stop_sentinel_path=sentinel_path,
        permission_prompt_listener=opts.permission_prompt_listener,
    )


def invoke_agent(
    config: AgentConfig,
    prompt_file: str,
    *,
    options: InvokeOptions | None = None,
    _clock: Clock | None = None,
) -> Iterator[str]:
    """Invoke agent, yield parsed output lines as they arrive.

    Args:
        config: Agent configuration specifying command and flags.
        prompt_file: Path to PROMPT.md file to pass to agent.
        options: Optional invocation options.
        _clock: Injectable Clock for testing; production callers omit this.

    Yields:
        Raw agent output lines (before parsing).

    Raises:
        AgentInvocationError: If agent exits with non-zero code.
    """
    opts = _prepare_interactive_claude_options(options or InvokeOptions(), config)
    runtime = resolve_invocation_runtime(
        config,
        opts.extra_env,
        opts.workspace_path,
        system_prompt_file=opts.system_prompt_file,
    )
    runtime_env = runtime.agent_env
    mcp_endpoint = runtime.mcp_endpoint
    allowed_mcp_tool_names = _provider_allowed_mcp_tool_names(config, mcp_endpoint)
    cmd = _build_command(
        config,
        prompt_file,
        options=_BuildCommandOptions(
            model_flag=opts.model_flag,
            session_id=opts.session_id,
            verbose=opts.verbose,
            pure=opts.pure,
            mcp_endpoint=mcp_endpoint,
            allowed_mcp_tool_names=allowed_mcp_tool_names,
            system_prompt_file=opts.system_prompt_file,
            workspace_path=opts.workspace_path,
            initial_session_id=opts.initial_session_id,
            settings_json=opts.settings_json,
            stop_sentinel_path=opts.stop_sentinel_path,
        ),
    )
    logger.info("Invoking agent: {}", _command_for_log(config, cmd, prompt_file))

    label_scope = None
    if runtime_env is not None:
        label_scope = runtime_env.get(str(AGENT_LABEL_SCOPE_ENV))
    registry = _make_child_registry(opts)
    execution_strategy = strategy_for_transport(
        _agent_transport(config),
        label_scope=label_scope,
        registry=registry,
    )
    liveness_probe = DefaultLivenessProbe(registry=registry)
    monitor = _start_workspace_monitor(opts.workspace_path)
    policy = _policy_from_options(opts)

    ctx = _AgentRunCtx(
        config=config,
        show_progress=opts.show_progress,
        extra_env=runtime_env,
        workspace_path=opts.workspace_path,
        policy=policy,
        execution_strategy=execution_strategy,
        liveness_probe=liveness_probe,
        waiting_listener=opts.waiting_listener,
        monitor=monitor,
        required_artifact=opts.required_artifact,
        clock=_clock,
    )
    try:
        transport = _agent_transport(config)
        if transport == AgentTransport.CLAUDE_INTERACTIVE:
            extras = _PtyExtras(
                expected_session_id=opts.session_id or opts.initial_session_id,
                stop_sentinel_path=opts.stop_sentinel_path,
            )
            lines_iter = _run_pty_and_read_lines(cmd, ctx, extras)
        else:
            lines_iter = _run_subprocess_and_read_lines(cmd, ctx)
        yield from lines_iter

        _log_workspace_completion(monitor)
    finally:
        _stop_workspace_monitor(monitor)


def _make_child_registry(opts: InvokeOptions) -> ChildLivenessRegistry:
    """Create a new per-invoke ChildLivenessRegistry using config-driven TTL values."""
    return ChildLivenessRegistry(
        progress_ttl=opts.child_progress_ttl_seconds
        if opts.child_progress_ttl_seconds is not None
        else CHILD_PROGRESS_TTL_SECONDS,
        heartbeat_ttl=opts.child_heartbeat_ttl_seconds
        if opts.child_heartbeat_ttl_seconds is not None
        else CHILD_HEARTBEAT_TTL_SECONDS,
        stale_label_ttl=opts.child_stale_label_ttl_seconds
        if opts.child_stale_label_ttl_seconds is not None
        else CHILD_STALE_LABEL_TTL_SECONDS,
        exit_reconcile=opts.child_exit_reconcile_seconds
        if opts.child_exit_reconcile_seconds is not None
        else CHILD_EXIT_RECONCILE_SECONDS,
    )


def _start_workspace_monitor(workspace_path: Path | None) -> WorkspaceMonitor | None:
    """Start workspace monitoring if path provided.

    Args:
        workspace_path: Optional path to workspace.

    Returns:
        WorkspaceMonitor instance or None.
    """
    if workspace_path is None:
        return None
    monitor = WorkspaceMonitor(workspace_path)
    monitor.start()
    return monitor


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


@dataclass(frozen=True)
class _PtyExtras:
    expected_session_id: str | None = None
    stop_sentinel_path: Path | None = None
    permission_prompt_listener: Callable[[str], None] | None = None


@dataclass(frozen=True)
class _ProcessReaderCtx:
    policy: TimeoutPolicy
    execution_strategy: GenericExecutionStrategy | OpenCodeExecutionStrategy | None = None
    liveness_probe: LivenessProbe | None = None
    waiting_listener: WaitingStatusListener | None = None
    monitor: WorkspaceMonitor | None = None


class _ProcessLineReader:
    """Reads lines from a subprocess stdout in a background thread."""

    def __init__(self, handle: ManagedProcess, ctx: _ProcessReaderCtx, clock: Clock) -> None:
        self._handle = handle
        self._policy = ctx.policy
        self._strategy = ctx.execution_strategy or GenericExecutionStrategy()
        self._probe = ctx.liveness_probe or DefaultLivenessProbe()
        self._waiting_listener = ctx.waiting_listener
        self._monitor = ctx.monitor
        self._clock = clock
        self._lines_queue: list[str] = []
        self._lines_lock = threading.Lock()
        self._lines_event = threading.Event()
        self._terminal_counter: list[int] = [0]
        self._last_activity_meaningful: list[bool] = [False]
        self._last_hard_stop: list[WaitingStatusEvent | None] = [None]
        self._reader_done: list[bool] = [False]
        self._last_activity_kind = "none"
        self._unsubscribe = get_process_manager().register_listener(self._on_process_event)

    def _on_process_event(self, event: ProcessEvent) -> None:
        if (
            event.record.label is not None
            and event.record.label.startswith("invoke:")
            and event.new_status in _TERMINAL_PROCESS_STATUSES
        ):
            self._terminal_counter[0] += 1

    def _on_waiting_event(self, evt: WaitingStatusEvent) -> None:
        if evt.kind == WaitingStatusKind.HARD_STOP:
            self._last_hard_stop[0] = evt
        if self._waiting_listener is not None:
            self._waiting_listener(evt)

    def _corroborate(self) -> CorroborationSnapshot:
        ws_count: int | None = self._monitor.event_count if self._monitor is not None else None
        oldest_secs: float | None = None
        scoped_active: bool | None = None
        scoped_count: int | None = None
        try:
            desc_count, desc_oldest = self._handle.descendant_snapshot()
            scoped_count = desc_count
            scoped_active = desc_count > 0
            oldest_secs = desc_oldest
        except Exception:
            logger.debug("corroborator: process scan failed (suppressed)")
        alive_by: AliveBy | None = None
        reg = cast("ChildLivenessRegistry | None", getattr(self._strategy, "_registry", None))
        if reg is not None:
            try:
                label_prefix = cast(
                    "str | None",
                    getattr(self._strategy, "_active_label_prefix", lambda: None)(),
                )
                reg_snap = reg.snapshot(label_prefix or "")
                verdict = classify_child_snapshot(reg_snap, has_os_descendants=bool(scoped_active))
                alive_by = verdict.alive_by
            except Exception:
                logger.debug("corroborator: registry snapshot failed (suppressed)")
        elif scoped_active:
            alive_by = AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS
        return CorroborationSnapshot(
            workspace_event_count=ws_count,
            oldest_child_seconds=oldest_secs,
            scoped_child_active=scoped_active,
            scoped_child_count=scoped_count,
            terminal_child_events_total=self._terminal_counter[0],
            last_activity_was_meaningful=self._last_activity_meaningful[0],
            alive_by=alive_by,
        )

    def _read_thread(self) -> None:
        stdout_pipe = cast("IO[str] | None", self._handle.stdout)
        if stdout_pipe is None:
            with self._lines_lock:
                self._reader_done[0] = True
            self._lines_event.set()
            return
        try:
            for line in stdout_pipe:
                with self._lines_lock:
                    self._lines_queue.append(line)
                    self._lines_event.set()
        except Exception:
            pass
        finally:
            with self._lines_lock:
                self._reader_done[0] = True
            self._lines_event.set()

    def _classify_quiet(self) -> AgentExecutionState:
        try:
            return self._strategy.classify_quiet(self._handle, self._probe)
        except Exception:
            logger.opt(exception=True).debug(
                "idle watchdog: classify_quiet raised; defaulting to WAITING_ON_CHILD"
            )
            return AgentExecutionState.WAITING_ON_CHILD

    def _check_fire(
        self, watchdog: IdleWatchdog, verdict: WatchdogVerdict
    ) -> tuple[list[str], _IdleStreamTimeoutError] | None:
        if verdict != WatchdogVerdict.FIRE:
            return None
        assert (
            self._policy.idle_timeout_seconds is not None
            or self._policy.max_session_seconds is not None
        )
        fire_reason = watchdog.last_fire_reason
        assert fire_reason is not None
        timeout_val = (
            self._policy.max_session_seconds
            if fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED
            else self._policy.idle_timeout_seconds
        )
        assert timeout_val is not None
        logger.warning(
            "idle watchdog firing reason={} elapsed={}s cumulative_waiting={}s "
            "last_activity_kind={} resume_safe=false",
            fire_reason,
            round(self._clock.monotonic(), 1),
            round(watchdog.cumulative_waiting_on_child_seconds, 1),
            self._last_activity_kind,
        )
        with self._lines_lock:
            pending = list(self._lines_queue)
            self._lines_queue.clear()
        self._handle.terminate(grace_period_s=0.5)
        hs_event = self._last_hard_stop[0]
        hard_stop_diag = hs_event.diagnostic if hs_event is not None else None
        return pending, _IdleStreamTimeoutError(
            timeout_val,
            fire_reason,
            diagnostic=hard_stop_diag,
        )

    def _run_drain_window(
        self, watchdog: IdleWatchdog, drain_deadline: float | None
    ) -> tuple[list[str], _IdleStreamTimeoutError] | None:
        while True:
            result = self._check_fire(
                watchdog, watchdog.evaluate(classify_quiet=self._classify_quiet)
            )
            if result is not None:
                return result
            if drain_deadline is not None and self._clock.monotonic() >= drain_deadline:
                return None
            if self._policy.idle_timeout_seconds is None:
                return None
            self._clock.wait_for_event(self._lines_event, self._policy.idle_poll_interval_seconds)

    def read_lines(self) -> Iterator[str]:
        reader = threading.Thread(target=self._read_thread, daemon=True)
        reader.start()
        watchdog = IdleWatchdog(
            self._policy,
            self._clock,
            listener=self._on_waiting_event,
            corroborator=self._corroborate,
        )
        try:
            while True:
                self._lines_event.clear()
                queued_line: str | None = None
                is_done = False
                with self._lines_lock:
                    if self._lines_queue:
                        queued_line = self._lines_queue.pop(0)
                    elif self._reader_done[0]:
                        is_done = True

                if queued_line is not None:
                    activity_signal = self._strategy.classify_activity_line(queued_line)
                    if activity_signal is not None:
                        self._last_activity_kind = str(activity_signal.kind)
                        self._last_activity_meaningful[0] = (
                            activity_signal.kind not in _NON_MEANINGFUL_ACTIVITY_KINDS
                        )
                        watchdog.record_activity()
                    else:
                        self._last_activity_meaningful[0] = False
                    self._strategy.observe_line(queued_line)
                    yield queued_line
                    result = self._check_fire(
                        watchdog, watchdog.evaluate(classify_quiet=self._classify_quiet)
                    )
                    if result is not None:
                        pending_lines, exc = result
                        yield from pending_lines
                        raise exc
                    continue

                if is_done:
                    drain_deadline = (
                        self._clock.monotonic() + self._policy.drain_window_seconds
                        if self._policy.drain_window_seconds
                        else None
                    )
                    result = self._run_drain_window(watchdog, drain_deadline)
                    if result is not None:
                        pending_lines, exc = result
                        yield from pending_lines
                        raise exc
                    break

                result = self._check_fire(
                    watchdog, watchdog.evaluate(classify_quiet=self._classify_quiet)
                )
                if result is not None:
                    pending_lines, exc = result
                    yield from pending_lines
                    raise exc

                self._clock.wait_for_event(
                    self._lines_event, self._policy.idle_poll_interval_seconds
                )

            reader.join(timeout=10)
        finally:
            self._unsubscribe()


def _run_subprocess_and_read_lines(
    cmd: list[str],
    ctx: _AgentRunCtx,
) -> Iterator[str]:
    """Run subprocess and yield output lines.

    Args:
        cmd: Command to execute.
        ctx: Agent run context with configuration and options.

    Yields:
        Output lines from the subprocess.
    """
    clock: Clock = ctx.clock or SystemClock()
    handle = get_process_manager().spawn(
        cmd,
        SpawnOptions(
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=None,
            cwd=str(ctx.workspace_path) if ctx.workspace_path is not None else None,
            env=_subprocess_env(ctx.extra_env),
            start_new_session=True,
            label=f"invoke:{_agent_command_name(ctx.config)}",
            text=True,
        ),
    )
    strategy = ctx.execution_strategy or GenericExecutionStrategy()
    probe: LivenessProbe = ctx.liveness_probe or DefaultLivenessProbe()
    with handle:
        stdout_pipe = handle.stdout
        if stdout_pipe is None:
            msg = "Failed to capture stdout"
            raise AgentInvocationError(_agent_command_name(ctx.config), -1, msg)

        reader_ctx = _ProcessReaderCtx(
            policy=ctx.policy,
            execution_strategy=ctx.execution_strategy,
            liveness_probe=ctx.liveness_probe,
            waiting_listener=ctx.waiting_listener,
            monitor=ctx.monitor,
        )
        lines_iter = _ProcessLineReader(handle, reader_ctx, clock).read_lines()
        parsed_output: deque[str] = deque(maxlen=_MAX_PARSED_OUTPUT_LINES)
        explicit_completion_seen = False
        captured_session_id: str | None = None
        try:
            if ctx.show_progress:
                agent_name = _agent_command_name(ctx.config)
                progress_iter = cast(
                    "Iterator[str]",
                    tqdm(
                        lines_iter,
                        desc=f"[{agent_name}]",
                        unit="line",
                        leave=False,
                        file=sys.stdout,
                    ),
                )
                for line in progress_iter:
                    stripped_line = line.rstrip()
                    parsed_output.append(stripped_line)
                    explicit_completion_seen = explicit_completion_seen or (
                        _EXPLICIT_COMPLETION_MARKER in stripped_line
                    )
                    session_id = _extract_session_id_from_line(stripped_line)
                    if session_id is not None:
                        captured_session_id = session_id
                    yield line
            else:
                for line in lines_iter:
                    stripped_line = line.rstrip()
                    parsed_output.append(stripped_line)
                    explicit_completion_seen = explicit_completion_seen or (
                        _EXPLICIT_COMPLETION_MARKER in stripped_line
                    )
                    session_id = _extract_session_id_from_line(stripped_line)
                    if session_id is not None:
                        captured_session_id = session_id
                    yield line

            # Post-EOF: wait for subprocess to exit within policy budget.
            # Prevents hanging when a subprocess closes stdout but never calls exit().
            post_exit = PostExitWatchdog(ctx.policy, clock)
            verdict = post_exit.wait_for_process_exit(lambda: handle.poll() is not None)
            if verdict == PostExitVerdict.FIRE_PROCESS_EXIT_HANG:
                handle.terminate(grace_period_s=0.5)
                raise _IdleStreamTimeoutError(
                    ctx.policy.process_exit_wait_seconds,
                    WatchdogFireReason.PROCESS_EXIT_HANG,
                )
        except _IdleStreamTimeoutError as exc:
            raise AgentInactivityTimeoutError(
                _agent_command_name(ctx.config),
                exc.timeout_seconds,
                _bounded_output_lines(
                    tuple(parsed_output),
                    explicit_completion_seen=explicit_completion_seen,
                ),
                InactivityTimeoutOpts(reason=exc.reason, diagnostic=exc.diagnostic),
            ) from exc

        _check_process_result(
            handle,
            _agent_command_name(ctx.config),
            list(parsed_output),
            _CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=ctx.workspace_path,
                liveness_probe=probe,
                policy=ctx.policy,
                required_artifact=ctx.required_artifact,
                explicit_completion_seen=explicit_completion_seen,
                captured_session_id=captured_session_id,
            ),
            _clock=clock,
        )


def _run_pty_and_read_lines(
    cmd: list[str],
    ctx: _AgentRunCtx,
    extras: _PtyExtras | None = None,
) -> Iterator[str]:
    _extras = extras or _PtyExtras()
    expected_session_id = _extras.expected_session_id
    if _extras.stop_sentinel_path is not None:
        with contextlib.suppress(FileNotFoundError):
            _extras.stop_sentinel_path.unlink()
    clock: Clock = ctx.clock or SystemClock()
    handle = get_process_manager().spawn_pty(
        cmd,
        PtySpawnOptions(
            cwd=str(ctx.workspace_path) if ctx.workspace_path is not None else None,
            env=_subprocess_env(ctx.extra_env),
            label=f"invoke:{_agent_command_name(ctx.config)}",
        ),
    )
    strategy = ctx.execution_strategy or GenericExecutionStrategy()
    probe: LivenessProbe = ctx.liveness_probe or DefaultLivenessProbe()
    with handle:
        lines_iter = _PtyLineReader(
            handle,
            _agent_command_name(ctx.config),
            ctx,
            clock,
            _extras,
        ).read_lines()
        parsed_output: deque[str] = deque(maxlen=_MAX_PARSED_OUTPUT_LINES)
        explicit_completion_seen = False
        captured_session_id: str | None = None
        try:
            if ctx.show_progress:
                agent_name = _agent_command_name(ctx.config)
                progress_iter = cast(
                    "Iterator[str]",
                    tqdm(
                        lines_iter,
                        desc=f"[{agent_name}]",
                        unit="line",
                        leave=False,
                        file=sys.stdout,
                    ),
                )
                for line in progress_iter:
                    stripped_line = line.rstrip()
                    parsed_output.append(stripped_line)
                    explicit_completion_seen = explicit_completion_seen or (
                        _EXPLICIT_COMPLETION_MARKER in stripped_line
                    )
                    session_id = _extract_session_id_from_line(stripped_line)
                    if session_id is not None:
                        captured_session_id = session_id
                    yield line
            else:
                for line in lines_iter:
                    stripped_line = line.rstrip()
                    parsed_output.append(stripped_line)
                    explicit_completion_seen = explicit_completion_seen or (
                        _EXPLICIT_COMPLETION_MARKER in stripped_line
                    )
                    session_id = _extract_session_id_from_line(stripped_line)
                    if session_id is not None:
                        captured_session_id = session_id
                    yield line

            if captured_session_id is None:
                captured_session_id = expected_session_id

            post_exit = PostExitWatchdog(ctx.policy, clock)
            verdict = post_exit.wait_for_process_exit(lambda: handle.poll() is not None)
            if verdict == PostExitVerdict.FIRE_PROCESS_EXIT_HANG:
                handle.terminate(grace_period_s=0.5)
                raise _IdleStreamTimeoutError(
                    ctx.policy.process_exit_wait_seconds,
                    WatchdogFireReason.PROCESS_EXIT_HANG,
                )
        except _IdleStreamTimeoutError as exc:
            raise AgentInactivityTimeoutError(
                _agent_command_name(ctx.config),
                exc.timeout_seconds,
                _bounded_output_lines(
                    tuple(parsed_output),
                    explicit_completion_seen=explicit_completion_seen,
                ),
                InactivityTimeoutOpts(reason=exc.reason, diagnostic=exc.diagnostic),
            ) from exc

        _check_process_result(
            handle,
            _agent_command_name(ctx.config),
            list(parsed_output),
            _CompletionCheckOptions(
                execution_strategy=strategy,
                workspace_path=ctx.workspace_path,
                liveness_probe=probe,
                policy=ctx.policy,
                required_artifact=ctx.required_artifact,
                explicit_completion_seen=explicit_completion_seen,
                captured_session_id=captured_session_id,
            ),
            _clock=clock,
        )


def _split_complete_vt_lines(text: str) -> tuple[list[str], str]:
    lines = text.splitlines(keepends=True)
    pending = lines.pop() if lines and not lines[-1].endswith(("\n", "\r")) else ""
    return lines, pending


def _pending_vt_snapshot_line(text: str) -> str | None:
    normalized = normalize_vt_text(text).strip()
    if not normalized:
        return None
    return f"{normalized}\n"


def _visible_tui_text(text: str) -> str:
    return normalize_vt_text(text).strip()


def _extract_choice_menu_state(text: str) -> _ChoiceMenuState | None:
    visible = normalize_vt_text(text)
    lines = [line.strip() for line in visible.splitlines() if line.strip()]
    if not lines:
        return None
    options: list[_ChoiceMenuOption] = []
    prompt: str | None = None
    confirm_footer: str | None = None
    for line in lines:
        if "enter to confirm" in line.lower():
            confirm_footer = line
            continue
        match = _CHOICE_MENU_OPTION_RE.match(line)
        if match is not None:
            index = int(str(match.group("index")))
            label = str(match.group("label")).strip()
            selected = match.group("prefix") is not None
            options.append(_ChoiceMenuOption(index=index, label=label, selected=selected))
            continue
        if prompt is None:
            prompt = line
    if prompt is None or confirm_footer is None or not options:
        return None
    selected_index = next((i for i, option in enumerate(options) if option.selected), None)
    return _ChoiceMenuState(
        prompt=prompt,
        options=tuple(options),
        selected_index=selected_index,
        confirm_footer=confirm_footer,
    )


def _menu_navigation_response(
    state: _ChoiceMenuState,
    preferred_index: int | None,
) -> str | None:
    if preferred_index is None or state.selected_index is None:
        return None
    delta = preferred_index - state.selected_index
    if delta > 0:
        return ("\x1b[B" * delta) + "\r"
    if delta < 0:
        return ("\x1b[A" * abs(delta)) + "\r"
    return "\r"


def _plan_choice_menu_response(text: str) -> str | None:
    state = _extract_choice_menu_state(text)
    if state is None:
        return None
    preferred_index = state.selected_index
    for i, option in enumerate(state.options):
        label = option.label.lower()
        if label.startswith("yes") and "default" not in label:
            preferred_index = i
            break
    return _menu_navigation_response(state, preferred_index)


def _approval_option_score(label: str) -> int | None:
    lowered = label.lower()
    if any(
        token in lowered for token in ("no", "cancel", "deny", "reject", "block", "exit", "skip")
    ):
        return None
    score = 0
    if any(token in lowered for token in ("allow", "approve", "grant", "authorize", "yes")):
        score += 4
    if any(token in lowered for token in ("once", "this time", "now")):
        score += 2
    if any(token in lowered for token in ("always", "default", "session", "permanent")):
        score -= 3
    return score if score > 0 else None


def _best_permission_option(state: _ChoiceMenuState) -> tuple[int, str] | None:
    preferred_index: int | None = None
    preferred_score: int | None = None
    preferred_label: str | None = None
    for i, option in enumerate(state.options):
        score = _approval_option_score(option.label)
        if score is None:
            continue
        if preferred_score is None or score > preferred_score:
            preferred_index = i
            preferred_score = score
            preferred_label = option.label
    if preferred_index is None or preferred_label is None:
        return None
    return preferred_index, preferred_label


def _plan_fuzzy_permission_menu_response(text: str) -> str | None:
    state = _extract_choice_menu_state(text)
    if state is None:
        return None
    best = _best_permission_option(state)
    if best is None:
        return None
    preferred_index, _ = best
    return _menu_navigation_response(state, preferred_index)


def _permission_prompt_action_message(
    text: str,
    *,
    auto_mode_prompt_seen: bool,
) -> str | None:
    state = _extract_choice_menu_state(
        text
        if auto_mode_prompt_seen
        else f"Enable auto mode?\n{text}"
        if _is_auto_mode_menu_snapshot(text)
        else text
    )
    if state is None:
        return None
    selected_label: str | None = None
    if auto_mode_prompt_seen or _is_auto_mode_menu_snapshot(text):
        for option in state.options:
            lowered = option.label.lower()
            if lowered.startswith("yes") and "default" not in lowered:
                selected_label = option.label
                break
    else:
        best = _best_permission_option(state)
        if best is not None:
            _, selected_label = best
    if selected_label is None:
        return None
    prompt_summary = state.prompt
    return f"Ralph auto-answered permission prompt: {prompt_summary} → {selected_label}"


def _is_auto_mode_menu_snapshot(text: str) -> bool:
    visible = normalize_vt_text(text)
    lowered = [line.strip().lower() for line in visible.splitlines() if line.strip()]
    if not any("enter to confirm" in line for line in lowered):
        return False
    return any("yes, and make it my default mode" in line for line in lowered) and any(
        "yes, enable auto mode" in line for line in lowered
    )


def _write_pty_input(writer: IO[bytes], text: str, *, lock: threading.Lock | None = None) -> None:
    if lock is None:
        writer.write(text.encode("utf-8"))
        writer.flush()
        return
    with lock:
        writer.write(text.encode("utf-8"))
        writer.flush()


def _is_permission_prompt_line(text: str) -> bool:
    stripped = _visible_tui_text(text)
    if _extract_choice_menu_state(text) is not None:
        return True
    return any(pattern.search(stripped) is not None for pattern in _PERMISSION_PROMPT_PATTERNS)


def _interactive_auto_response_for_prompt(
    text: str,
    *,
    auto_mode_prompt_seen: bool,
) -> str | None:
    if auto_mode_prompt_seen or _is_auto_mode_menu_snapshot(text):
        return _plan_choice_menu_response(
            text if auto_mode_prompt_seen else f"Enable auto mode?\n{text}"
        )
    return _plan_fuzzy_permission_menu_response(text)


def _claude_projects_root() -> Path:
    home = Path.home()
    return home / ".claude" / "projects"


def _find_claude_transcript_path(session_id: str) -> Path | None:
    projects_root = _claude_projects_root()
    if not projects_root.exists():
        return None
    target_name = f"{session_id}.jsonl"
    for candidate_root in projects_root.iterdir():
        candidate = candidate_root / target_name
        if candidate.is_file():
            return candidate
    return None


def _extract_message_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _transcript_lines_from_assistant_content(content: list[object]) -> list[str]:
    lines: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", ""))
        if item_type == "tool_use":
            lines.append(f"claude tool: {item.get('name', 'tool')!s}\n")
        elif item_type == "text":
            text = str(item.get("text", "")).strip()
            if text:
                lines.append(f"{text}\n")
    return lines


def _transcript_lines_from_user_content(content: list[object]) -> list[str]:
    lines: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_dict = cast("dict[str, object]", item)
        if item_dict.get("type") != "tool_result":
            continue
        result_content = _extract_message_text(item_dict.get("content"))
        if result_content:
            lines.append(f"claude tool result: {result_content}\n")
    return lines


def _transcript_lines_from_message(
    message: object, extractor: Callable[[list[object]], list[str]]
) -> list[str]:
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []
    return extractor(content)


def _transcript_lines_from_event(raw_line: str) -> list[str]:
    try:
        parsed = cast("object", json.loads(raw_line))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, dict):
        return []
    obj = cast("dict[str, object]", parsed)
    event_type = str(obj.get("type", ""))
    if event_type in {"permission-mode", ""}:
        return []
    if event_type == "assistant":
        return _transcript_lines_from_message(
            obj.get("message"), _transcript_lines_from_assistant_content
        )
    if event_type == "user":
        return _transcript_lines_from_message(
            obj.get("message"), _transcript_lines_from_user_content
        )
    return []


class _PtyLineReader:
    def __init__(
        self,
        handle: ManagedPtyProcess,
        agent_name: str,
        ctx: _AgentRunCtx,
        clock: Clock,
        extras: _PtyExtras | None,
    ) -> None:
        _extras = extras or _PtyExtras()
        self._handle = handle
        self._agent_name = agent_name
        self._policy = ctx.policy
        self._monitor = ctx.monitor
        self._clock = clock
        self._strategy: GenericExecutionStrategy | OpenCodeExecutionStrategy = (
            ctx.execution_strategy or GenericExecutionStrategy()
        )
        self._probe: LivenessProbe = ctx.liveness_probe or DefaultLivenessProbe()
        self._waiting_listener = ctx.waiting_listener
        self._expected_session_id = _extras.expected_session_id
        self._stop_sentinel_path = _extras.stop_sentinel_path
        self._permission_prompt_listener = _extras.permission_prompt_listener
        self._lines_queue: list[str] = []
        self._lines_lock = threading.Lock()
        self._lines_event = threading.Event()
        self._monitor_stop = threading.Event()
        self._terminal_counter: list[int] = [0]
        self._last_meaningful: list[bool] = [False]
        self._last_hard_stop: list[WaitingStatusEvent | None] = [None]
        self._reader_done: list[bool] = [False]
        self._input_writer = os.fdopen(os.dup(handle.master_fd), "wb", buffering=0)
        self._input_writer_lock = threading.Lock()
        self._auto_mode_prompt_seen = False
        self._auto_response_menu_seen = False
        self._auto_mode_menu_screen: str | None = None
        self._last_auto_mode_response_at: float | None = None
        self._last_auto_mode_menu_seen_at: float | None = None
        self._pending_permission_prompt_line: str | None = None
        self._pending_permission_prompt_started_at: float | None = None

    def _start_thread(self, target: Callable[[], None]) -> threading.Thread:
        t = threading.Thread(target=target, daemon=True)
        t.start()
        return t

    def _on_process_event(self, event: ProcessEvent) -> None:
        if (
            event.record.label is not None
            and event.record.label.startswith("invoke:")
            and event.new_status in _TERMINAL_PROCESS_STATUSES
        ):
            self._terminal_counter[0] += 1

    def _on_waiting_event(self, evt: WaitingStatusEvent) -> None:
        if evt.kind == WaitingStatusKind.HARD_STOP:
            self._last_hard_stop[0] = evt
        if self._waiting_listener is not None:
            self._waiting_listener(evt)

    def _corroborate(self) -> CorroborationSnapshot:
        ws_count: int | None = self._monitor.event_count if self._monitor is not None else None
        oldest_secs: float | None = None
        scoped_active: bool | None = None
        scoped_count: int | None = None
        try:
            desc_count, desc_oldest = self._handle.descendant_snapshot()
            scoped_count = desc_count
            scoped_active = desc_count > 0
            oldest_secs = desc_oldest
        except Exception:
            logger.debug("corroborator: PTY process scan failed (suppressed)")
        alive_by: AliveBy | None = None
        reg = cast("ChildLivenessRegistry | None", getattr(self._strategy, "_registry", None))
        if reg is not None:
            try:
                label_prefix = cast(
                    "str | None",
                    getattr(self._strategy, "_active_label_prefix", lambda: None)(),
                )
                reg_snap = reg.snapshot(label_prefix or "")
                verdict = classify_child_snapshot(
                    reg_snap, has_os_descendants=bool(scoped_active)
                )
                alive_by = verdict.alive_by
            except Exception:
                logger.debug("corroborator: PTY registry snapshot failed (suppressed)")
        elif scoped_active:
            alive_by = AliveBy.OS_DESCENDANT_ONLY_STALE_PROGRESS
        return CorroborationSnapshot(
            workspace_event_count=ws_count,
            oldest_child_seconds=oldest_secs,
            scoped_child_active=scoped_active,
            scoped_child_count=scoped_count,
            terminal_child_events_total=self._terminal_counter[0],
            last_activity_was_meaningful=self._last_meaningful[0],
            alive_by=alive_by,
        )

    def _read_thread(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending = ""
        last_snapshot: str | None = None
        try:
            while True:
                if self._handle.poll() is not None and not wait_for_master_readable(
                    self._handle.master_fd, 0.01
                ):
                    break
                if not wait_for_master_readable(self._handle.master_fd, 0.05):
                    continue
                chunk = read_master_chunk(self._handle.master_fd)
                if not chunk:
                    break
                pending += decoder.decode(chunk)
                completed, pending = _split_complete_vt_lines(pending)
                if completed:
                    with self._lines_lock:
                        self._lines_queue.extend(completed)
                        self._lines_event.set()
                    last_snapshot = None
                    continue
                snapshot_line = _pending_vt_snapshot_line(pending)
                if snapshot_line is not None and snapshot_line != last_snapshot:
                    with self._lines_lock:
                        self._lines_queue.append(snapshot_line)
                        self._lines_event.set()
                    last_snapshot = snapshot_line
            tail = pending + decoder.decode(b"", final=True)
            if tail:
                snapshot_line = _pending_vt_snapshot_line(tail)
                if snapshot_line is None:
                    snapshot_line = tail
                with self._lines_lock:
                    self._lines_queue.append(snapshot_line)
                    self._lines_event.set()
        except Exception:
            pass
        finally:
            with self._lines_lock:
                self._reader_done[0] = True
            self._lines_event.set()
            self._monitor_stop.set()

    def _transcript_thread(self) -> None:
        if self._expected_session_id is None:
            return
        transcript_path: Path | None = None
        file_obj = None
        while not self._monitor_stop.is_set():
            if transcript_path is None:
                transcript_path = _find_claude_transcript_path(self._expected_session_id)
                if transcript_path is None:
                    self._monitor_stop.wait(0.1)
                    continue
                file_obj = transcript_path.open("r", encoding="utf-8", errors="replace")
            assert file_obj is not None
            line = file_obj.readline()
            if not line:
                self._monitor_stop.wait(0.1)
                continue
            emitted_lines = _transcript_lines_from_event(line)
            if emitted_lines:
                with self._lines_lock:
                    self._lines_queue.extend(emitted_lines)
                    self._lines_event.set()
        if file_obj is not None:
            file_obj.close()

    def _sentinel_thread(self) -> None:
        if self._stop_sentinel_path is None:
            return
        while not self._monitor_stop.is_set():
            if self._stop_sentinel_path.exists():
                with self._lines_lock:
                    self._lines_queue.append(_TURN_BOUNDARY_MARKER + "\n")
                    self._lines_event.set()
                with contextlib.suppress(OSError):
                    _write_pty_input(
                        self._input_writer, "/exit\r\n", lock=self._input_writer_lock
                    )
                return
            self._monitor_stop.wait(0.05)

    def _classify_quiet(self) -> AgentExecutionState:
        try:
            return self._strategy.classify_quiet(self._handle, self._probe)
        except Exception:
            logger.opt(exception=True).debug(
                "idle watchdog: classify_quiet raised for PTY runtime; "
                "defaulting to WAITING_ON_CHILD"
            )
            return AgentExecutionState.WAITING_ON_CHILD

    def _check_fire(
        self,
        watchdog: IdleWatchdog,
        verdict: WatchdogVerdict,
    ) -> tuple[list[str], _IdleStreamTimeoutError] | None:
        if verdict != WatchdogVerdict.FIRE:
            return None
        assert (
            self._policy.idle_timeout_seconds is not None
            or self._policy.max_session_seconds is not None
        )
        fire_reason = watchdog.last_fire_reason
        assert fire_reason is not None
        timeout_val = (
            self._policy.max_session_seconds
            if fire_reason == WatchdogFireReason.SESSION_CEILING_EXCEEDED
            else self._policy.idle_timeout_seconds
        )
        assert timeout_val is not None
        with self._lines_lock:
            pending_lines = list(self._lines_queue)
            self._lines_queue.clear()
        self._handle.terminate(grace_period_s=0.5)
        hs_event = self._last_hard_stop[0]
        hard_stop_diag = hs_event.diagnostic if hs_event is not None else None
        return pending_lines, _IdleStreamTimeoutError(
            timeout_val,
            fire_reason,
            diagnostic=hard_stop_diag,
        )

    def _run_drain_window(
        self,
        watchdog: IdleWatchdog,
        drain_deadline: float | None,
    ) -> tuple[list[str], _IdleStreamTimeoutError] | None:
        while True:
            fire_result = self._check_fire(
                watchdog, watchdog.evaluate(classify_quiet=self._classify_quiet)
            )
            if fire_result is not None:
                return fire_result
            if drain_deadline is not None and self._clock.monotonic() >= drain_deadline:
                return None
            if self._policy.idle_timeout_seconds is None:
                return None
            self._clock.wait_for_event(
                self._lines_event, self._policy.idle_poll_interval_seconds
            )

    def _observe_queued_line(self, queued_line: str) -> None:
        visible_line = _visible_tui_text(queued_line)
        if _extract_choice_menu_state(queued_line) is not None:
            self._auto_mode_menu_screen = queued_line
            self._last_auto_mode_menu_seen_at = self._clock.monotonic()
        prompt_line_seen = "enable auto mode?" in visible_line.lower()
        menu_snapshot_seen = _is_auto_mode_menu_snapshot(queued_line)
        if prompt_line_seen or menu_snapshot_seen:
            self._auto_mode_prompt_seen = True
            self._auto_mode_menu_screen = queued_line
            self._last_auto_mode_menu_seen_at = self._clock.monotonic()
        auto_response = _interactive_auto_response_for_prompt(
            queued_line,
            auto_mode_prompt_seen=self._auto_mode_prompt_seen,
        )
        if auto_response is not None:
            self._auto_response_menu_seen = True
            self._auto_mode_menu_screen = queued_line
            self._last_auto_mode_menu_seen_at = self._clock.monotonic()
        if _is_permission_prompt_line(queued_line):
            self._pending_permission_prompt_line = queued_line.rstrip()
            self._pending_permission_prompt_started_at = self._clock.monotonic()
        else:
            self._pending_permission_prompt_line = None
            self._pending_permission_prompt_started_at = None

    def _maybe_send_auto_response(self) -> None:
        if self._auto_response_menu_seen:
            now = self._clock.monotonic()
            menu_quiescent = (
                self._last_auto_mode_menu_seen_at is not None
                and (now - self._last_auto_mode_menu_seen_at) >= _MENU_QUIESCENCE_SECONDS
            )
            if menu_quiescent and (
                self._last_auto_mode_response_at is None
                or (now - self._last_auto_mode_response_at) >= 1.0
            ):
                with contextlib.suppress(OSError):
                    screen = self._auto_mode_menu_screen or ""
                    response = (
                        _interactive_auto_response_for_prompt(
                            screen,
                            auto_mode_prompt_seen=self._auto_mode_prompt_seen,
                        )
                        or "\r"
                    )
                    _write_pty_input(
                        self._input_writer, response, lock=self._input_writer_lock
                    )
                    action_message = _permission_prompt_action_message(
                        screen,
                        auto_mode_prompt_seen=self._auto_mode_prompt_seen,
                    )
                    if action_message is not None:
                        logger.info(action_message)
                        if self._permission_prompt_listener is not None:
                            self._permission_prompt_listener(action_message)
                    self._pending_permission_prompt_line = None
                    self._pending_permission_prompt_started_at = None
                    self._last_auto_mode_response_at = now
        prompt_grace_exceeded = (
            self._pending_permission_prompt_started_at is not None
            and (
                self._clock.monotonic() - self._pending_permission_prompt_started_at
            ) >= _MENU_QUIESCENCE_SECONDS
        )
        if (
            self._pending_permission_prompt_line is not None
            and prompt_grace_exceeded
            and not self._auto_response_menu_seen
        ):
            self._handle.terminate(grace_period_s=0.5)
            raise InteractivePermissionPromptError(
                self._agent_name,
                [self._pending_permission_prompt_line],
            )

    def _on_interrupt(self) -> None:
        self._monitor_stop.set()
        with contextlib.suppress(Exception):
            self._handle.close()

    def _cleanup(
        self,
        readers: list[threading.Thread],
        unsubscribe: Callable[[], None],
        interrupted: bool,
    ) -> None:
        self._monitor_stop.set()
        timeouts = (
            0.1 if interrupted else 10,
            0.1 if interrupted else 1,
            0.1 if interrupted else 1,
        )
        for reader, timeout in zip(readers, timeouts, strict=True):
            reader.join(timeout=timeout)
        with contextlib.suppress(Exception):
            self._input_writer.close()
        if self._stop_sentinel_path is not None:
            with contextlib.suppress(FileNotFoundError):
                self._stop_sentinel_path.unlink()
        unsubscribe()

    def _handle_queued_line(
        self, queued_line: str, watchdog: IdleWatchdog
    ) -> Iterator[str]:
        self._observe_queued_line(queued_line)
        activity_signal = self._strategy.classify_activity_line(queued_line)
        if activity_signal is not None:
            self._last_meaningful[0] = (
                activity_signal.kind not in _NON_MEANINGFUL_ACTIVITY_KINDS
            )
            watchdog.record_activity()
        else:
            self._last_meaningful[0] = False
        self._strategy.observe_line(queued_line)
        yield queued_line
        fire_result = self._check_fire(
            watchdog, watchdog.evaluate(classify_quiet=self._classify_quiet)
        )
        if fire_result is not None:
            pending_lines, exc = fire_result
            yield from pending_lines
            raise exc

    def _handle_done_path(self, watchdog: IdleWatchdog) -> Iterator[str]:
        drain_deadline = (
            self._clock.monotonic() + self._policy.drain_window_seconds
            if self._policy.drain_window_seconds
            else None
        )
        drain_result = self._run_drain_window(watchdog, drain_deadline)
        if drain_result is not None:
            pending_lines, exc = drain_result
            yield from pending_lines
            raise exc

    def _idle_check_and_wait(self, watchdog: IdleWatchdog) -> Iterator[str]:
        self._maybe_send_auto_response()
        fire_result = self._check_fire(
            watchdog, watchdog.evaluate(classify_quiet=self._classify_quiet)
        )
        if fire_result is not None:
            pending_lines, exc = fire_result
            yield from pending_lines
            raise exc
        self._clock.wait_for_event(
            self._lines_event, self._policy.idle_poll_interval_seconds
        )

    def read_lines(self) -> Iterator[str]:
        reader = self._start_thread(self._read_thread)
        transcript_reader = self._start_thread(self._transcript_thread)
        sentinel_reader = self._start_thread(self._sentinel_thread)
        watchdog = IdleWatchdog(
            self._policy,
            self._clock,
            listener=self._on_waiting_event,
            corroborator=self._corroborate,
        )
        unsubscribe = get_process_manager().register_listener(self._on_process_event)
        interrupted = [False]
        try:
            while True:
                self._lines_event.clear()
                queued_line: str | None = None
                is_done = False
                with self._lines_lock:
                    if self._lines_queue:
                        queued_line = self._lines_queue.pop(0)
                    elif self._reader_done[0]:
                        is_done = True

                if queued_line is not None:
                    yield from self._handle_queued_line(queued_line, watchdog)
                    continue

                if is_done:
                    yield from self._handle_done_path(watchdog)
                    break

                yield from self._idle_check_and_wait(watchdog)
        except BaseException:
            interrupted[0] = True
            self._on_interrupt()
            raise
        finally:
            self._cleanup(
                [reader, transcript_reader, sentinel_reader], unsubscribe, interrupted[0]
            )


def _subprocess_env(extra_env: dict[str, str] | None) -> dict[str, str]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return env


def resolve_invocation_runtime(
    config: AgentConfig,
    extra_env: dict[str, str] | None,
    workspace_path: Path | None,
    *,
    _base_env: Mapping[str, str] | None = None,
    system_prompt_file: str | None = None,
) -> ResolvedInvocationRuntime:
    """Build the runtime configuration needed to launch an agent.

    Resolves transport-specific environment variables, MCP server configuration,
    and endpoint address from ``config`` and ``extra_env``.  Returns a
    ``ResolvedInvocationRuntime`` whose fields are ready to pass to the
    subprocess launcher.
    """
    _env = _base_env if _base_env is not None else cast("Mapping[str, str]", os.environ)
    runtime_env = dict(extra_env or {})
    server_env: dict[str, str] = {}
    endpoint = runtime_env.get(MCP_ENDPOINT_ENV)

    transport = _agent_transport(config)
    if transport == AgentTransport.OPENCODE:
        if not endpoint:
            return ResolvedInvocationRuntime(agent_env=runtime_env or None)
        provider_config, upstreams = build_opencode_provider_config(
            runtime_env.get("OPENCODE_CONFIG_CONTENT") or _env.get("OPENCODE_CONFIG_CONTENT"),
            endpoint,
        )
        runtime_env["OPENCODE_CONFIG_CONTENT"] = provider_config
        mcp_toml = mcp_toml_as_upstreams(workspace_path)
        merged_upstreams = merge_mcp_toml_into_upstreams(upstreams, mcp_toml)
        set_upstream_mcp_config(runtime_env, merged_upstreams)
        set_upstream_mcp_config(server_env, merged_upstreams)
        return ResolvedInvocationRuntime(
            agent_env=runtime_env,
            server_env=server_env or None,
            mcp_endpoint=endpoint,
        )
    if transport == AgentTransport.CODEX:
        if not endpoint and system_prompt_file is None:
            return ResolvedInvocationRuntime(agent_env=runtime_env or None)
        codex_home, upstreams = prepare_codex_home_with_upstreams(
            endpoint,
            workspace_path=workspace_path,
            existing_home=runtime_env.get("CODEX_HOME") or _env.get("CODEX_HOME"),
            system_prompt_file=system_prompt_file,
        )
        runtime_env["CODEX_HOME"] = codex_home
        mcp_toml = mcp_toml_as_upstreams(workspace_path)
        merged_upstreams = merge_mcp_toml_into_upstreams(upstreams, mcp_toml)
        set_upstream_mcp_config(runtime_env, merged_upstreams)
        set_upstream_mcp_config(server_env, merged_upstreams)
        return ResolvedInvocationRuntime(
            agent_env=runtime_env,
            server_env=server_env or None,
            mcp_endpoint=endpoint,
        )
    if transport in (AgentTransport.CLAUDE, AgentTransport.CLAUDE_INTERACTIVE):
        if endpoint:
            existing = load_existing_claude_upstream_servers(workspace_path)
            mcp_toml = mcp_toml_as_upstreams(workspace_path)
            merged_upstreams = merge_mcp_toml_into_upstreams(existing, mcp_toml)
            set_upstream_mcp_config(runtime_env, merged_upstreams)
            set_upstream_mcp_config(server_env, merged_upstreams)
        return ResolvedInvocationRuntime(
            agent_env=runtime_env or None,
            server_env=server_env or None,
            mcp_endpoint=endpoint,
        )

    if not endpoint:
        return ResolvedInvocationRuntime(agent_env=runtime_env or None)

    raise UnsupportedMcpTransportError(
        f"Agent transport '{transport}' does not declare how to receive Ralph MCP wiring"
    )


def _runtime_extra_env(
    config: AgentConfig,
    extra_env: dict[str, str] | None,
    workspace_path: Path | None,
    *,
    system_prompt_file: str | None = None,
) -> dict[str, str] | None:
    return resolve_invocation_runtime(
        config,
        extra_env,
        workspace_path,
        system_prompt_file=system_prompt_file,
    ).agent_env


def _agent_transport(config: AgentConfig) -> AgentTransport:
    transport = config.transport
    if transport is None:
        return AgentTransport.GENERIC
    return transport


def _agent_command_name(config: AgentConfig) -> str:
    return config.cmd.split()[0]


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
        pure=rt.pure,
        session_id=rt.session_id,
        system_prompt_file=rt.system_prompt_file,
        waiting_listener=rt.waiting_listener,
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
            else None  # No max set or constraint would be violated; disable
        ),
    )


def _read_lines_from_process(
    handle: ManagedProcess,
    *,
    ctx: _ProcessReaderCtx,
    _clock: Clock | None = None,
) -> Iterator[str]:
    clock: Clock = _clock or SystemClock()
    return _ProcessLineReader(handle, ctx, clock).read_lines()


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


@dataclass(frozen=True)
class _CompletionCheckOptions:
    execution_strategy: GenericExecutionStrategy | OpenCodeExecutionStrategy | None = None
    workspace_path: Path | None = None
    # LivenessProbe for checking whether Ralph-tracked child agents are still active.
    # When None, classify_exit falls back to handle.has_live_descendants() only.
    liveness_probe: LivenessProbe | None = None
    # TimeoutPolicy governs all post-exit timeout dimensions (parent grace, descendant wait).
    # Uses a factory default so callers that don't need timeouts can omit it.
    policy: TimeoutPolicy = field(default_factory=lambda: TimeoutPolicy(idle_timeout_seconds=None))
    required_artifact: RequiredArtifact | None = None
    explicit_completion_seen: bool = False
    captured_session_id: str | None = None


def _wait_for_completion_grace(
    handle: ManagedProcess | ManagedPtyProcess,
    opts: _CompletionCheckOptions,
    parsed_output: list[str],
    *,
    clock: Clock | None = None,
) -> AgentExecutionState:
    """Wait up to policy.parent_exit_grace_seconds for completion signals or children to appear.

    Polls evaluate_completion + classify_exit at policy.descendant_wait_poll_seconds intervals.
    Returns:
      TERMINAL_COMPLETE if completion signals appear during the grace window.
      WAITING_ON_CHILD if children appear (caller must escalate to descendant wait).
      RESUMABLE_CONTINUE if grace deadline elapses with no signals and no children.
    """
    assert opts.workspace_path is not None
    workspace_path = opts.workspace_path
    execution_strategy = opts.execution_strategy
    assert execution_strategy is not None

    effective_clock: Clock = clock or SystemClock()
    probe = opts.liveness_probe or DefaultLivenessProbe()

    def classify_exit_state() -> AgentExecutionState:
        signals = evaluate_completion(
            workspace_path,
            _bounded_output_lines(
                parsed_output,
                explicit_completion_seen=opts.explicit_completion_seen,
            ),
            required_artifact=opts.required_artifact,
        )
        return execution_strategy.classify_exit(handle, signals, liveness_probe=probe)

    post_exit = PostExitWatchdog(opts.policy, effective_clock)
    verdict = post_exit.wait_parent_exit_grace(classify_exit_state)
    if verdict == PostExitVerdict.SIGNALS_PRESENT:
        return AgentExecutionState.TERMINAL_COMPLETE
    if verdict == PostExitVerdict.CHILDREN_ACTIVE:
        return AgentExecutionState.WAITING_ON_CHILD
    return AgentExecutionState.RESUMABLE_CONTINUE


def _wait_for_descendants_then_recheck(
    handle: ManagedProcess | ManagedPtyProcess,
    opts: _CompletionCheckOptions,
    parsed_output: list[str],
    *,
    clock: Clock | None = None,
) -> AgentExecutionState:
    """Wait for descendant processes to finish, then re-evaluate completion signals.

    Polls the execution strategy's classify_exit at policy.descendant_wait_poll_seconds
    intervals until either the tree is quiet (state != WAITING_ON_CHILD) or the deadline
    elapses. This allows artifacts written by background subagents to become visible before
    OpenCodeResumableExitError is raised.

    Args:
        handle: Completed parent process handle.
        opts: Completion check options including liveness_probe and policy.
        parsed_output: Raw NDJSON output lines from the agent.
        clock: Injectable Clock; defaults to SystemClock.

    Returns:
        TERMINAL_COMPLETE if tree quiessed and completion signals present.
        RESUMABLE_CONTINUE if deadline elapsed with children still alive (fallback to
        retry rather than silent success). WAITING_ON_CHILD is only returned during
        the active polling loop, never after deadline.
    """
    assert opts.workspace_path is not None
    workspace_path = opts.workspace_path
    execution_strategy = opts.execution_strategy
    assert execution_strategy is not None

    effective_clock: Clock = clock or SystemClock()
    probe = opts.liveness_probe or DefaultLivenessProbe()

    def classify_exit_state() -> AgentExecutionState:
        signals = evaluate_completion(
            workspace_path,
            _bounded_output_lines(
                parsed_output,
                explicit_completion_seen=opts.explicit_completion_seen,
            ),
            required_artifact=opts.required_artifact,
        )
        return execution_strategy.classify_exit(handle, signals, liveness_probe=probe)

    post_exit = PostExitWatchdog(opts.policy, effective_clock)
    verdict = post_exit.wait_descendant_quiesce(classify_exit_state)
    if verdict == PostExitVerdict.SIGNALS_PRESENT:
        return AgentExecutionState.TERMINAL_COMPLETE
    if verdict == PostExitVerdict.QUIESCED_NO_SIGNALS:
        return AgentExecutionState.RESUMABLE_CONTINUE
    # FIRE_DESCENDANT_HANG: WAITING_ON_CHILD persisted for full deadline.
    # Fall back to RESUMABLE_CONTINUE so the caller raises OpenCodeResumableExitError.
    return AgentExecutionState.RESUMABLE_CONTINUE


def _check_process_result(
    handle: ManagedProcess | ManagedPtyProcess,
    agent_name: str,
    parsed_output: list[str] | None = None,
    check_options: _CompletionCheckOptions | None = None,
    *,
    _clock: Clock | None = None,
) -> None:
    """Check subprocess return code and raise error if non-zero.

    For session-continuing agents, exit 0 without required completion evidence raises
    OpenCodeResumableExitError so the runner can continue the same session.
    When the process exits but child agents are still running, this function
    waits up to policy.descendant_wait_timeout_seconds for the tree to quiesce
    before re-evaluating completion signals.

    Args:
        handle: Completed managed process.
        agent_name: Name of the agent.
        _clock: Injectable Clock for testing; production callers omit this.

    Raises:
        AgentInvocationError: If process exited with non-zero code.
        OpenCodeResumableExitError: If the agent session exited without required
            completion evidence and no child agents are still running.
    """
    returncode = int(handle.returncode or 0)
    if returncode != 0:
        stderr_pipe = cast("IO[str] | None", getattr(handle, "stderr", None))
        stderr = stderr_pipe.read() if stderr_pipe is not None else "(unable to read stderr)"
        logger.error("Agent exited with code {}: {}", returncode, stderr)
        raise AgentInvocationError(
            agent_name,
            returncode,
            stderr,
            _bounded_output_lines(
                parsed_output or [],
                explicit_completion_seen=(
                    check_options.explicit_completion_seen if check_options is not None else False
                ),
            ),
        )

    opts = check_options
    if (
        opts is not None
        and opts.execution_strategy is not None
        and opts.execution_strategy.supports_session_continuation()
        and opts.workspace_path is not None
    ):
        bounded_output = _bounded_output_lines(
            parsed_output or [],
            explicit_completion_seen=opts.explicit_completion_seen,
        )
        signals = evaluate_completion(
            opts.workspace_path,
            bounded_output,
            required_artifact=opts.required_artifact,
        )
        # First classification: check completion signals and immediate child status
        exit_state = opts.execution_strategy.classify_exit(
            handle, signals, liveness_probe=opts.liveness_probe
        )

        # When parent exits with no children visible at exit time but no completion
        # signals either, run a mandatory grace window that polls for late artifacts,
        # explicit_complete markers, or background children that hadn't yet registered
        # with the ProcessManager. This eliminates the false-positive retry where
        # OpenCode is killed within milliseconds of exit despite ongoing background work.
        if exit_state == AgentExecutionState.RESUMABLE_CONTINUE:
            exit_state = _wait_for_completion_grace(
                handle,
                opts,
                bounded_output,
                clock=_clock,
            )

        # If children appeared (either at exit time or during the grace window),
        # wait for the tree to quiesce before declaring failure.
        if exit_state == AgentExecutionState.WAITING_ON_CHILD:
            exit_state = _wait_for_descendants_then_recheck(
                handle,
                opts,
                bounded_output,
                clock=_clock,
            )

        if exit_state == AgentExecutionState.RESUMABLE_CONTINUE:
            session_id = opts.captured_session_id or extract_session_id(bounded_output)
            raise OpenCodeResumableExitError(agent_name, session_id=session_id)


def _stop_workspace_monitor(monitor: WorkspaceMonitor | None) -> None:
    """Stop workspace monitoring.

    Args:
        monitor: Workspace monitor to stop.
    """
    if monitor is not None:
        monitor.stop()


def _build_command(
    config: AgentConfig,
    prompt_file: str,
    *,
    options: _BuildCommandOptions | None = None,
) -> list[str]:
    """Build the command line for agent invocation.

    Args:
        config: Agent configuration.
        prompt_file: Path to prompt file.
        model_flag: Optional model flag override.
        session_id: Optional session ID for session-enabled agents.
        verbose: Whether to include verbose flag.

    Returns:
        List of command arguments.
    """
    build_options = options or _BuildCommandOptions()
    transport = _agent_transport(config)
    if build_options.mcp_endpoint and transport == AgentTransport.GENERIC:
        raise UnsupportedMcpTransportError(
            "Ralph MCP endpoint provided for agent without a supported transport adapter"
        )

    if transport == AgentTransport.OPENCODE:
        return _build_opencode_command(
            config,
            prompt_file,
            options=build_options,
        )

    if transport == AgentTransport.CODEX:
        return _build_codex_command(
            config,
            prompt_file,
            options=build_options,
        )

    if transport == AgentTransport.CLAUDE_INTERACTIVE:
        return _build_claude_interactive_command(
            config,
            prompt_file,
            options=build_options,
        )

    cmd = config.cmd.split()
    if transport == AgentTransport.CLAUDE and config.output_flag is not None:
        cmd.append(config.output_flag)

    if config.print_flag:
        cmd.append(config.print_flag)

    if config.streaming_flag:
        cmd.append(config.streaming_flag)

    if config.session_flag and build_options.session_id:
        cmd.extend(config.session_flag.format(build_options.session_id).split())

    cmd.extend(_split_optional_flag(config.yolo_flag))

    if build_options.verbose and config.verbose_flag:
        cmd.append(config.verbose_flag)

    _extend_claude_transport_flags(cmd, transport, build_options)

    if transport == AgentTransport.CLAUDE and build_options.system_prompt_file:
        cmd.extend(["--append-system-prompt-file", build_options.system_prompt_file])

    effective_model = build_options.model_flag or config.model_flag
    if effective_model:
        cmd.extend(effective_model.split())

    _append_transport_prompt_arg(cmd, transport, prompt_file, build_options)
    return cmd


def _build_claude_interactive_command(
    config: AgentConfig,
    prompt_file: str,
    *,
    options: _BuildCommandOptions,
) -> list[str]:
    cmd = config.cmd.split()
    cmd.extend(_split_optional_flag(config.yolo_flag))
    _extend_claude_transport_flags(cmd, AgentTransport.CLAUDE_INTERACTIVE, options)
    if options.verbose and config.verbose_flag:
        cmd.append(config.verbose_flag)
    if config.session_flag and options.session_id:
        cmd.extend(config.session_flag.format(options.session_id).split())
    elif options.initial_session_id is not None:
        cmd.extend(["--session-id", options.initial_session_id])
    if options.settings_json is not None:
        cmd.extend(["--settings", options.settings_json])
    if options.system_prompt_file:
        cmd.extend(["--append-system-prompt-file", options.system_prompt_file])
    effective_model = options.model_flag or config.model_flag
    if effective_model:
        cmd.extend(effective_model.split())
    _append_transport_prompt_arg(cmd, AgentTransport.CLAUDE_INTERACTIVE, prompt_file, options)
    return cmd


def _extend_claude_transport_flags(
    cmd: list[str],
    transport: AgentTransport,
    build_options: _BuildCommandOptions,
) -> None:
    if (
        transport not in (AgentTransport.CLAUDE, AgentTransport.CLAUDE_INTERACTIVE)
        or build_options.mcp_endpoint is None
    ):
        return

    # Claude/CCS non-interactive MCP mode is brittle around `--tools ""` combined
    # with `--allowedTools`. We only emit the tool restriction flags when live MCP
    # tool discovery succeeds and yields a non-empty allowlist; otherwise we keep the
    # strict MCP server isolation but avoid the known empty-tool edge case entirely.
    cmd.extend(
        [
            "--mcp-config",
            claude_mcp_config(
                build_options.mcp_endpoint,
                workspace_path=build_options.workspace_path,
            ),
            "--strict-mcp-config",
        ]
    )
    if build_options.allowed_mcp_tool_names:
        cmd.extend(
            [
                "--tools",
                "",
                "--allowedTools",
                ",".join(build_options.allowed_mcp_tool_names),
            ]
        )


def _resolve_prompt_path(prompt_file: str, workspace_path: Path | None) -> Path:
    prompt_path = Path(prompt_file)
    if prompt_path.is_absolute() or workspace_path is None:
        return prompt_path
    return workspace_path / prompt_path


def _sidecar_path_for_prompt(prompt_path: Path) -> Path | None:
    if not prompt_path.name.endswith("_prompt.md"):
        return None
    normalized = prompt_path.stem.removesuffix("_prompt")
    return prompt_path.parent / f"{normalized}_multimodal_handoff.json"


def _read_multimodal_sidecar(
    prompt_file: str,
    workspace_path: Path | None,
) -> list[dict[str, object]] | None:
    resolved = _resolve_prompt_path(prompt_file, workspace_path)
    sidecar = _sidecar_path_for_prompt(resolved)
    if sidecar is None or not sidecar.exists():
        return None
    try:
        data: dict[str, object] = json.loads(sidecar.read_text(encoding="utf-8"))
        artifacts = data.get("artifacts")
        if isinstance(artifacts, list) and artifacts:
            return cast("list[dict[str, object]]", artifacts)
        return None
    except Exception:
        return None


def _build_multimodal_appendix(artifacts: list[dict[str, object]]) -> str:
    lines = [
        "",
        "",
        "## Multimodal Artifacts",
        "",
        "The following artifacts are available via Ralph's MCP surface.",
        "Retrieve each artifact by calling the read_media tool"
        " with path=<ralph://media/...> replay handle:",
        "",
    ]
    for entry in artifacts:
        modality = entry.get("modality", "unknown")
        title = entry.get("title", "untitled")
        uri = entry.get("uri", "")
        delivery = entry.get("delivery", "resource_reference_replay")
        block_type = entry.get("block_type", "")
        reason = entry.get("reason", "")
        failure_kind = entry.get("failure_kind", "")
        lines.append(f"- [{modality}] {title}")
        lines.append(f"  path={uri}")
        lines.append(f"  Delivery: {delivery}")
        if block_type:
            lines.append(f"  Block-type: {block_type}")
        if failure_kind == "unsupported_runtime_seam":
            reason_suffix = f" Reason: {reason}" if reason else ""
            lines.append(
                f"  Note: the upstream artifact exists but cannot be delivered"
                f" through the active runtime seam.{reason_suffix}"
                " Do not use read_media, replay handles, or typed blocks for this artifact."
            )
        elif delivery == "resource_reference_replay":
            lines.append(
                "  Note: if the artifact is from a previous session it may not be"
                " replayable; read_media will return an explicit"
                " missing_replay_source failure in that case."
            )
        elif delivery == "typed_block":
            block_type_hint = f" (block_type={block_type!r})" if block_type else ""
            lines.append(
                f"  Note: call read_media with this path to receive a typed block"
                f"{block_type_hint} for direct delivery to the model."
            )
        elif delivery == "resource_reference":
            lines.append(
                "  Note: this artifact references an external URI; the model may"
                " access it directly via the URI without calling read_media."
            )
        elif delivery == "unsupported":
            reason_suffix = f" Reason: {reason}" if reason else ""
            lines.append(
                f"  Note: this modality is unsupported by the active provider;"
                f"{reason_suffix}"
                " read_media will return an explicit unsupported_modality failure."
            )
        lines.append("")
    return "\n".join(lines)


def _append_transport_prompt_arg(
    cmd: list[str],
    transport: AgentTransport,
    prompt_file: str,
    build_options: _BuildCommandOptions,
) -> None:
    if (
        transport in (AgentTransport.CLAUDE, AgentTransport.CLAUDE_INTERACTIVE)
        and build_options.mcp_endpoint
    ):
        cmd.append("--")
        resolved_prompt = _resolve_prompt_path(prompt_file, build_options.workspace_path)
        prompt_text = resolved_prompt.read_text(encoding="utf-8")
        artifacts = _read_multimodal_sidecar(prompt_file, build_options.workspace_path)
        if artifacts:
            prompt_text += _build_multimodal_appendix(artifacts)
        cmd.append(prompt_text)
        return
    cmd.append(prompt_file)


def _provider_allowed_mcp_tool_names(
    config: AgentConfig,
    endpoint: str | None,
) -> tuple[str, ...]:
    if endpoint is None or _agent_transport(config) not in (
        AgentTransport.CLAUDE,
        AgentTransport.CLAUDE_INTERACTIVE,
    ):
        return ()
    try:
        visible_tool_names = _discover_http_mcp_tool_names(endpoint)
    except (PreflightError, ValueError) as exc:
        logger.warning("Failed to discover Ralph MCP tools for provider allowlist: {}", exc)
        return ()
    return tuple(claude_tool_name(tool_name) for tool_name in visible_tool_names)


def _discover_http_mcp_tool_names(endpoint: str) -> list[str]:
    target = parse_http_endpoint(endpoint)
    initialize_response, session_id = post_http_jsonrpc_with_session(
        endpoint,
        target,
        initialize_request(),
    )
    ensure_no_preflight_error("HTTP MCP initialize", initialize_response.get("error"))
    initialized_response, session_id = post_http_jsonrpc_with_session(
        endpoint,
        target,
        initialized_notification(),
        session_id=session_id,
    )
    ensure_no_preflight_error(
        "HTTP MCP notifications/initialized", initialized_response.get("error")
    )
    tools_response, _ = post_http_jsonrpc_with_session(
        endpoint,
        target,
        tools_list_request(),
        session_id=session_id,
    )
    ensure_no_preflight_error("HTTP MCP tools/list", tools_response.get("error"))
    return extract_preflight_tool_names(tools_response.get("result"), "HTTP MCP")


def _build_opencode_command(
    config: AgentConfig,
    prompt_file: str,
    *,
    options: _BuildCommandOptions,
) -> list[str]:
    prompt_text = _resolve_prompt_path(prompt_file, options.workspace_path).read_text(
        encoding="utf-8"
    )
    artifacts = _read_multimodal_sidecar(prompt_file, options.workspace_path)
    if artifacts:
        prompt_text += _build_multimodal_appendix(artifacts)
    cmd = [_agent_command_name(config), "run"]
    if options.pure:
        cmd.append("--pure")
    cmd.extend(["--format", "json"])

    if config.session_flag and options.session_id:
        cmd.extend(config.session_flag.format(options.session_id).split())

    cmd.extend(_split_optional_flag(config.yolo_flag))

    if options.verbose and config.verbose_flag:
        cmd.append(config.verbose_flag)

    effective_model = options.model_flag or config.model_flag
    if effective_model:
        cmd.extend(_normalize_opencode_model_flag(effective_model))

    cmd.append(prompt_text)
    return cmd


def _build_codex_command(
    config: AgentConfig,
    prompt_file: str,
    *,
    options: _BuildCommandOptions,
) -> list[str]:
    prompt_text = _resolve_prompt_path(prompt_file, options.workspace_path).read_text(
        encoding="utf-8"
    )
    artifacts = _read_multimodal_sidecar(prompt_file, options.workspace_path)
    if artifacts:
        prompt_text += _build_multimodal_appendix(artifacts)
    cmd = config.cmd.split()
    if config.output_flag is not None:
        cmd.append(config.output_flag)

    cmd.extend(_split_optional_flag(config.yolo_flag))

    effective_model = options.model_flag or config.model_flag
    if effective_model:
        cmd.extend(effective_model.split())

    cmd.append(prompt_text)
    return cmd


def _command_for_log(config: AgentConfig, cmd: list[str], prompt_file: str) -> str:
    logged_cmd = list(cmd)
    if (
        _agent_transport(config)
        in {
            AgentTransport.OPENCODE,
            AgentTransport.CODEX,
            AgentTransport.CLAUDE,
            AgentTransport.CLAUDE_INTERACTIVE,
        }
        and logged_cmd
    ):
        logged_cmd[-1] = prompt_file
    return " ".join(logged_cmd)


def _normalize_opencode_model_flag(model_flag: str) -> list[str]:
    parts = model_flag.split()
    if len(parts) == _MODELED_FLAG_PARTS and parts[0] in {"-m", "--model"}:
        return [parts[0], parts[1].removeprefix("opencode/")]
    return parts


def _split_optional_flag(flag: str | None) -> list[str]:
    if not flag:
        return []
    return shlex.split(flag)


def check_agent_available(config: AgentConfig) -> bool:
    """Check if an agent command is available.

    Args:
        config: Agent configuration.

    Returns:
        True if agent command exists and is executable.
    """
    cmd = config.cmd.split()
    if not cmd:
        return False
    return shutil.which(cmd[0]) is not None
