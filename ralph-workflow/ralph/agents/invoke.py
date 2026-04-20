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

import json
import os
import shlex
import subprocess
import sys
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from loguru import logger
from tqdm import tqdm

from ralph.agents.transport_emit import (
    _build_opencode_provider_config,
    _claude_mcp_config,
    _load_existing_claude_upstream_servers,
    _mcp_toml_as_upstreams,
    _merge_mcp_toml_into_upstreams,
    _merge_opencode_config_content,  # noqa: F401  (re-exported for tests)
    _prepare_codex_home,  # noqa: F401  (re-exported for tests)
    _prepare_codex_home_with_upstreams,
    _set_upstream_mcp_config,
)
from ralph.config.enums import AgentTransport
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

_MODELED_FLAG_PARTS = 2
_IDLE_POLL_INTERVAL_SECONDS = 0.05


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
    """

    model_flag: str | None = None
    session_id: str | None = None
    verbose: bool = False
    show_progress: bool = True
    workspace_path: Path | None = None
    extra_env: dict[str, str] | None = None
    idle_timeout_seconds: float | None = 300.0
    pure: bool = False
    system_prompt_file: str | None = None


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


if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.config.models import AgentConfig

# Runtime imports with graceful fallback when watchdog is not available
try:
    from watchdog.events import FileSystemEventHandler as _WatchdogFileSystemEventHandlerClass
    from watchdog.observers import Observer as _WatchdogObserverClass

    _WATCHDOG_EVENTS_AVAILABLE = True
except ImportError:
    _WatchdogObserverClass = None  # type: ignore[assignment]  # watchdog is optional; None when not installed
    _WatchdogFileSystemEventHandlerClass = None  # type: ignore[assignment,misc]  # watchdog is optional; None when not installed
    _WATCHDOG_EVENTS_AVAILABLE = False


class _HasStop(Protocol):
    """Protocol for watchdog Observer-like objects that have a stop method."""

    def stop(self) -> None: ...
    def join(self, timeout: float | None = None) -> None: ...


@runtime_checkable
class _HasSrcPath(Protocol):
    """Protocol for watchdog events that expose a source path."""

    src_path: str


class _ObserverProtocol(_HasStop, Protocol):
    """Protocol for watchdog Observer-like objects used by this module."""

    def schedule(self, _event_handler: object, path: str, **kwargs: object) -> None: ...
    def start(self) -> None: ...


class _IdleStreamTimeoutError(RuntimeError):
    """Raised when an agent process stops producing output for too long."""

    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(f"Agent produced no output for {timeout_seconds:.0f}s")


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


class AgentInactivityTimeoutError(AgentInvocationError):
    """Raised when an agent stalls without producing output."""

    def __init__(
        self,
        agent_name: str,
        timeout_seconds: float,
        parsed_output: list[str] | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(
            agent_name,
            -1,
            f"Agent produced no output for {timeout_seconds:.0f}s",
            parsed_output,
        )


class UnsupportedMcpTransportError(RuntimeError):
    """Raised when MCP-backed execution is requested for an unsupported transport."""


def extract_session_id(raw_output: list[str]) -> str | None:
    """Extract a nested session identifier from raw NDJSON output lines."""
    for line in raw_output:
        try:
            parsed = cast("object", json.loads(line))
        except json.JSONDecodeError:
            continue
        session_id = _find_session_id(parsed)
        if session_id:
            return session_id
    return None


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
        self._seen_files: set[str] = set()

    def start(self) -> None:
        """Start monitoring the workspace for file changes."""
        if (
            _WatchdogObserverClass is None
            or _WatchdogFileSystemEventHandlerClass is None
            or not _WATCHDOG_EVENTS_AVAILABLE
        ):
            return

        class ChangeTracker(_WatchdogFileSystemEventHandlerClass):
            def __init__(self, monitor: WorkspaceMonitor) -> None:
                self._monitor = monitor

            def on_any_event(self, event: object) -> None:
                if isinstance(event, _HasSrcPath):
                    self._monitor.record_event(event.src_path)

        handler = ChangeTracker(self)
        self._observer = cast("_ObserverProtocol", _WatchdogObserverClass())
        self._observer.schedule(handler, str(self._workspace), recursive=True)
        self._observer.start()
        logger.debug("Started workspace monitoring: {}", self._workspace)

    def record_event(self, src_path: str) -> None:
        """Record a file change event.

        Args:
            src_path: Path to the changed file.
        """
        self._seen_files.add(src_path)
        self._event_count += 1

    def stop(self) -> None:
        """Stop monitoring the workspace."""
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
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
        return self._seen_files.copy()


def invoke_agent(
    config: AgentConfig,
    prompt_file: str,
    *,
    options: InvokeOptions | None = None,
) -> Iterator[str]:
    """Invoke agent, yield parsed output lines as they arrive.

    Args:
        config: Agent configuration specifying command and flags.
        prompt_file: Path to PROMPT.md file to pass to agent.
        options: Optional invocation options.

    Yields:
        Raw agent output lines (before parsing).

    Raises:
        AgentInvocationError: If agent exits with non-zero code.
    """
    opts = options or InvokeOptions()
    runtime_env = _runtime_extra_env(
        config,
        opts.extra_env,
        opts.workspace_path,
        system_prompt_file=opts.system_prompt_file,
    )
    mcp_endpoint = (runtime_env or {}).get("RALPH_MCP_ENDPOINT")
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
        ),
    )
    logger.info("Invoking agent: {}", _command_for_log(config, cmd, prompt_file))

    monitor = _start_workspace_monitor(opts.workspace_path)

    try:
        lines_iter = _run_subprocess_and_read_lines(
            cmd,
            config,
            opts.show_progress,
            runtime_env,
            opts.workspace_path,
            idle_timeout_seconds=opts.idle_timeout_seconds,
        )
        yield from lines_iter

        _log_workspace_completion(monitor)
    finally:
        _stop_workspace_monitor(monitor)


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


def _run_subprocess_and_read_lines(  # noqa: PLR0913
    cmd: list[str],
    config: AgentConfig,
    show_progress: bool,
    extra_env: dict[str, str] | None,
    workspace_path: Path | None,
    *,
    idle_timeout_seconds: float | None = None,
) -> Iterator[str]:
    """Run subprocess and yield output lines.

    Args:
        cmd: Command to execute.
        config: Agent configuration.
        show_progress: Whether to show progress bar.

    Yields:
        Output lines from the subprocess.
    """
    with subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=_subprocess_env(extra_env),
        cwd=str(workspace_path) if workspace_path is not None else None,
    ) as proc:
        if proc.stdout is None:
            msg = "Failed to capture stdout"
            raise AgentInvocationError(_agent_command_name(config), -1, msg)

        lines_iter = _read_lines_from_process(proc, idle_timeout_seconds=idle_timeout_seconds)
        parsed_output: list[str] = []
        if show_progress:
            agent_name = _agent_command_name(config)
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
            try:
                for line in progress_iter:
                    parsed_output.append(line.rstrip())
                    yield line
            except _IdleStreamTimeoutError as exc:
                raise AgentInactivityTimeoutError(
                    _agent_command_name(config),
                    exc.timeout_seconds,
                    parsed_output,
                ) from exc
        else:
            try:
                for line in lines_iter:
                    parsed_output.append(line.rstrip())
                    yield line
            except _IdleStreamTimeoutError as exc:
                raise AgentInactivityTimeoutError(
                    _agent_command_name(config),
                    exc.timeout_seconds,
                    parsed_output,
                ) from exc

        proc.wait()
        _check_process_result(proc, _agent_command_name(config), parsed_output)


def _subprocess_env(extra_env: dict[str, str] | None) -> dict[str, str]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return env


def _runtime_extra_env(
    config: AgentConfig,
    extra_env: dict[str, str] | None,
    workspace_path: Path | None,
    *,
    system_prompt_file: str | None = None,
) -> dict[str, str] | None:
    runtime_env = dict(extra_env or {})
    endpoint = runtime_env.get("RALPH_MCP_ENDPOINT")

    transport = _agent_transport(config)
    if transport == AgentTransport.OPENCODE:
        if not endpoint:
            return runtime_env or None
        provider_config, upstreams = _build_opencode_provider_config(
            runtime_env.get("OPENCODE_CONFIG_CONTENT") or os.environ.get("OPENCODE_CONFIG_CONTENT"),
            endpoint,
        )
        runtime_env["OPENCODE_CONFIG_CONTENT"] = provider_config
        mcp_toml = _mcp_toml_as_upstreams(workspace_path)
        _set_upstream_mcp_config(runtime_env, _merge_mcp_toml_into_upstreams(upstreams, mcp_toml))
        return runtime_env
    if transport == AgentTransport.CODEX:
        if not endpoint and system_prompt_file is None:
            return runtime_env or None
        codex_home, upstreams = _prepare_codex_home_with_upstreams(
            endpoint,
            workspace_path=workspace_path,
            existing_home=runtime_env.get("CODEX_HOME") or os.environ.get("CODEX_HOME"),
            system_prompt_file=system_prompt_file,
        )
        runtime_env["CODEX_HOME"] = codex_home
        mcp_toml = _mcp_toml_as_upstreams(workspace_path)
        _set_upstream_mcp_config(runtime_env, _merge_mcp_toml_into_upstreams(upstreams, mcp_toml))
        return runtime_env
    if transport == AgentTransport.CLAUDE:
        if endpoint:
            existing = _load_existing_claude_upstream_servers(workspace_path)
            mcp_toml = _mcp_toml_as_upstreams(workspace_path)
            _set_upstream_mcp_config(
                runtime_env, _merge_mcp_toml_into_upstreams(existing, mcp_toml)
            )
        return runtime_env

    if not endpoint:
        return runtime_env or None

    raise UnsupportedMcpTransportError(
        f"Agent transport '{transport}' does not declare how to receive Ralph MCP wiring"
    )


def _agent_transport(config: AgentConfig) -> AgentTransport:
    transport = config.transport
    if transport is None:
        return AgentTransport.GENERIC
    return transport


def _agent_command_name(config: AgentConfig) -> str:
    return config.cmd.split()[0]


def _read_lines_from_process(
    proc: subprocess.Popen[str],
    *,
    idle_timeout_seconds: float | None = None,
) -> Iterator[str]:
    """Read lines from subprocess stdout in a background thread.

    Args:
        proc: Running subprocess.
        idle_timeout_seconds: Optional maximum idle time without output.

    Yields:
        Lines from stdout.
    """
    lines_queue: list[str] = []
    lines_lock = threading.Lock()
    lines_event = threading.Event()
    last_activity = time.monotonic()

    def read_lines_thread() -> None:
        if proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                with lines_lock:
                    lines_queue.append(line)
        except Exception:
            pass
        finally:
            lines_event.set()

    reader = threading.Thread(target=read_lines_thread, daemon=True)
    reader.start()

    while True:
        queued_line: str | None = None
        with lines_lock:
            if lines_queue:
                queued_line = lines_queue.pop(0)

        if queued_line is not None:
            last_activity = time.monotonic()
            yield queued_line
            continue

        if lines_event.is_set():
            break

        if (
            idle_timeout_seconds is not None
            and time.monotonic() - last_activity >= idle_timeout_seconds
        ):
            _terminate_subprocess(proc)
            raise _IdleStreamTimeoutError(idle_timeout_seconds)

        lines_event.wait(_IDLE_POLL_INTERVAL_SECONDS)

    reader.join(timeout=10)


def _terminate_subprocess(proc: subprocess.Popen[str]) -> None:
    if _process_poll(proc) is not None:
        return

    with suppress(Exception):
        proc.terminate()
    _process_wait(proc, timeout=1)

    if _process_poll(proc) is not None:
        return

    with suppress(Exception):
        proc.kill()


def _process_poll(proc: subprocess.Popen[str]) -> int | None:
    with suppress(Exception):
        return proc.poll()
    return None


def _process_wait(proc: subprocess.Popen[str], *, timeout: float) -> None:
    try:
        proc.wait(timeout=timeout)
    except TypeError:
        return
    except Exception:
        return


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


def _check_process_result(
    proc: subprocess.Popen[str], agent_name: str, parsed_output: list[str] | None = None
) -> None:
    """Check subprocess return code and raise error if non-zero.

    Args:
        proc: Completed subprocess.
        agent_name: Name of the agent.

    Raises:
        AgentInvocationError: If process exited with non-zero code.
    """
    returncode = int(proc.returncode)
    if returncode == 0:
        return

    stderr_pipe = proc.stderr
    stderr = stderr_pipe.read() if stderr_pipe is not None else "(unable to read stderr)"
    logger.error("Agent exited with code {}: {}", returncode, stderr)
    raise AgentInvocationError(agent_name, returncode, stderr, parsed_output)


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

    cmd = config.cmd.split()
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


def _extend_claude_transport_flags(
    cmd: list[str],
    transport: AgentTransport,
    build_options: _BuildCommandOptions,
) -> None:
    if transport != AgentTransport.CLAUDE or build_options.mcp_endpoint is None:
        return

    # Claude/CCS non-interactive MCP mode is brittle around `--tools ""` combined
    # with `--allowedTools`. We only emit the tool restriction flags when live MCP
    # tool discovery succeeds and yields a non-empty allowlist; otherwise we keep the
    # strict MCP server isolation but avoid the known empty-tool edge case entirely.
    cmd.extend(
        [
            "--mcp-config",
            _claude_mcp_config(
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


def _append_transport_prompt_arg(
    cmd: list[str],
    transport: AgentTransport,
    prompt_file: str,
    build_options: _BuildCommandOptions,
) -> None:
    if transport == AgentTransport.CLAUDE and build_options.mcp_endpoint:
        cmd.append("--")
        resolved_prompt = _resolve_prompt_path(prompt_file, build_options.workspace_path)
        cmd.append(resolved_prompt.read_text(encoding="utf-8"))
        return
    cmd.append(prompt_file)


def _provider_allowed_mcp_tool_names(
    config: AgentConfig,
    endpoint: str | None,
) -> tuple[str, ...]:
    if endpoint is None or _agent_transport(config) != AgentTransport.CLAUDE:
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
    cmd = config.cmd.split()
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
        in {AgentTransport.OPENCODE, AgentTransport.CODEX, AgentTransport.CLAUDE}
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
    try:
        cmd = config.cmd.split()
        result = subprocess.run(
            ["which", cmd[0]],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0
    except Exception as exc:
        logger.warning("Failed to check agent availability: {}", exc)
        return False
