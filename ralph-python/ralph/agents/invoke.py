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
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from loguru import logger
from tqdm import tqdm

from ralph.config.enums import AgentTransport

_MODELED_FLAG_PARTS = 2
_RALPH_MCP_SERVER_NAME = "ralph_runtime"


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
    """

    model_flag: str | None = None
    session_id: str | None = None
    verbose: bool = False
    show_progress: bool = True
    workspace_path: Path | None = None
    extra_env: dict[str, str] | None = None
    pure: bool = False


@dataclass(frozen=True)
class _BuildCommandOptions:
    model_flag: str | None = None
    session_id: str | None = None
    verbose: bool = False
    pure: bool = False
    mcp_endpoint: str | None = None


if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.config.models import AgentConfig

# Runtime imports with graceful fallback when watchdog is not available
try:
    from watchdog.events import FileSystemEventHandler as _WatchdogFileSystemEventHandlerClass
    from watchdog.observers import Observer as _WatchdogObserverClass

    _WATCHDOG_EVENTS_AVAILABLE = True
except ImportError:
    _WatchdogObserverClass = None  # type: ignore[assignment]
    _WatchdogFileSystemEventHandlerClass = None  # type: ignore[assignment,misc]
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

    def schedule(self, event_handler: object, path: str, *, recursive: bool = False) -> None: ...
    def start(self) -> None: ...


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
        super().__init__(f"Agent '{agent_name}' failed with code {returncode}")


class UnsupportedMcpTransportError(RuntimeError):
    """Raised when MCP-backed execution is requested for an unsupported transport."""


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
    runtime_env = _runtime_extra_env(config, opts.extra_env, opts.workspace_path)
    cmd = _build_command(
        config,
        prompt_file,
        options=_BuildCommandOptions(
            model_flag=opts.model_flag,
            session_id=opts.session_id,
            verbose=opts.verbose,
            pure=opts.pure,
            mcp_endpoint=(runtime_env or {}).get("RALPH_MCP_ENDPOINT"),
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


def _run_subprocess_and_read_lines(
    cmd: list[str],
    config: AgentConfig,
    show_progress: bool,
    extra_env: dict[str, str] | None,
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
    ) as proc:
        if proc.stdout is None:
            msg = "Failed to capture stdout"
            raise AgentInvocationError(_agent_command_name(config), -1, msg)

        lines_iter = _read_lines_from_process(proc)
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
            yield from progress_iter
        else:
            yield from lines_iter

        proc.wait()
        _check_process_result(proc, _agent_command_name(config))


def _subprocess_env(extra_env: dict[str, str] | None) -> dict[str, str]:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return env


def _runtime_extra_env(
    config: AgentConfig,
    extra_env: dict[str, str] | None,
    workspace_path: Path | None,
) -> dict[str, str] | None:
    runtime_env = dict(extra_env or {})
    endpoint = runtime_env.get("RALPH_MCP_ENDPOINT")
    if not endpoint:
        return runtime_env or None

    transport = _agent_transport(config)
    if transport == AgentTransport.OPENCODE:
        runtime_env["OPENCODE_CONFIG_CONTENT"] = _merge_opencode_config_content(
            runtime_env.get("OPENCODE_CONFIG_CONTENT") or os.environ.get("OPENCODE_CONFIG_CONTENT"),
            endpoint,
        )
        return runtime_env
    if transport == AgentTransport.CODEX:
        runtime_env["CODEX_HOME"] = _prepare_codex_home(
            endpoint,
            workspace_path=workspace_path,
            existing_home=runtime_env.get("CODEX_HOME") or os.environ.get("CODEX_HOME"),
        )
        return runtime_env
    if transport == AgentTransport.CLAUDE:
        return runtime_env

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


def _prepare_codex_home(
    endpoint: str,
    *,
    workspace_path: Path | None,
    existing_home: str | None,
) -> str:
    codex_root = _allocate_codex_home_dir(workspace_path)
    codex_root.mkdir(parents=True, exist_ok=True)

    source_home = Path(existing_home).expanduser() if existing_home else Path.home() / ".codex"
    if source_home.exists():
        _mirror_codex_home(source_home, codex_root)
    source_config = source_home / "config.toml"
    base_config = source_config.read_text(encoding="utf-8") if source_config.exists() else ""
    injected_server = (
        f'[mcp_servers.{_RALPH_MCP_SERVER_NAME}]\nurl = "{endpoint}"\nenabled = true\n'
    )
    config_text = (
        f"{base_config.rstrip()}\n\n{injected_server}" if base_config.strip() else injected_server
    )
    (codex_root / "config.toml").write_text(config_text, encoding="utf-8")
    return str(codex_root)


def _mirror_codex_home(source_home: Path, codex_root: Path) -> None:
    for entry in source_home.iterdir():
        if entry.name == "config.toml":
            continue
        destination = codex_root / entry.name
        try:
            destination.symlink_to(entry, target_is_directory=entry.is_dir())
        except OSError:
            if entry.is_dir():
                shutil.copytree(entry, destination, dirs_exist_ok=True)
            else:
                shutil.copy2(entry, destination)


def _allocate_codex_home_dir(workspace_path: Path | None) -> Path:
    if workspace_path is None:
        return Path(tempfile.mkdtemp(prefix="ralph-codex-home-"))

    tmp_root = workspace_path / ".agent" / "tmp"
    tmp_root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="codex-home-", dir=str(tmp_root)))


def _claude_mcp_config(endpoint: str) -> str:
    config_payload: dict[str, dict[str, dict[str, str]]] = {
        "mcpServers": {
            _RALPH_MCP_SERVER_NAME: {
                "type": "http",
                "url": endpoint,
            }
        }
    }
    return json.dumps(
        config_payload,
        separators=(",", ":"),
    )


def _merge_opencode_config_content(existing: str | None, endpoint: str) -> str:
    config_obj = _parse_opencode_config_content(existing)

    mcp_section_obj = config_obj.setdefault("mcp", {})
    if not isinstance(mcp_section_obj, dict):
        mcp_section_obj = {}
        config_obj["mcp"] = mcp_section_obj
    mcp_section = cast("dict[str, object]", mcp_section_obj)
    mcp_section["ralph"] = {
        "type": "remote",
        "url": endpoint,
        "enabled": True,
        "timeout": 30000,
    }

    permission_section_obj = config_obj.setdefault("permission", {})
    if not isinstance(permission_section_obj, dict):
        permission_section_obj = {}
        config_obj["permission"] = permission_section_obj
    permission_section = cast("dict[str, object]", permission_section_obj)
    permission_section["ralph_*"] = "allow"

    config_obj.setdefault("$schema", "https://opencode.ai/config.json")
    return json.dumps(config_obj, sort_keys=True)


def _parse_opencode_config_content(existing: str | None) -> dict[str, object]:
    if not existing:
        return {}
    try:
        decoded: object = json.loads(existing)
    except json.JSONDecodeError:
        return {}
    if not isinstance(decoded, dict):
        return {}
    return cast("dict[str, object]", decoded)


def _read_lines_from_process(proc: subprocess.Popen[str]) -> Iterator[str]:
    """Read lines from subprocess stdout in a background thread.

    Args:
        proc: Running subprocess.

    Yields:
        Lines from stdout.
    """
    lines_queue: list[str] = []
    lines_lock = threading.Lock()
    lines_event = threading.Event()

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
        with lines_lock:
            if lines_queue:
                yield lines_queue.pop(0)
            elif lines_event.is_set():
                break
            else:
                continue

    reader.join(timeout=10)


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


def _check_process_result(proc: subprocess.Popen[str], agent_name: str) -> None:
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
    raise AgentInvocationError(agent_name, returncode, stderr)


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

    cmd = config.cmd.split()
    cmd.append(config.output_flag)

    if config.print_flag:
        cmd.append(config.print_flag)

    if config.streaming_flag:
        cmd.append(config.streaming_flag)

    if config.session_flag and build_options.session_id:
        cmd.extend(config.session_flag.format(build_options.session_id).split())

    if config.yolo_flag:
        cmd.append(config.yolo_flag)

    if build_options.verbose and config.verbose_flag:
        cmd.append(config.verbose_flag)

    if transport == AgentTransport.CLAUDE and build_options.mcp_endpoint:
        cmd.extend(
            [
                "--mcp-config",
                _claude_mcp_config(build_options.mcp_endpoint),
            ]
        )

    effective_model = build_options.model_flag or config.model_flag
    if effective_model:
        cmd.extend(effective_model.split())

    cmd.append(prompt_file)

    return cmd


def _build_opencode_command(
    config: AgentConfig,
    prompt_file: str,
    *,
    options: _BuildCommandOptions,
) -> list[str]:
    prompt_text = Path(prompt_file).read_text(encoding="utf-8")
    cmd = [_agent_command_name(config), "run"]
    if options.pure:
        cmd.append("--pure")
    cmd.extend(["--format", "json"])

    if config.session_flag and options.session_id:
        cmd.extend(config.session_flag.format(options.session_id).split())

    if config.yolo_flag:
        cmd.append(config.yolo_flag)

    if options.verbose and config.verbose_flag:
        cmd.append(config.verbose_flag)

    effective_model = options.model_flag or config.model_flag
    if effective_model:
        cmd.extend(_normalize_opencode_model_flag(effective_model))

    cmd.append(prompt_text)
    return cmd


def _command_for_log(config: AgentConfig, cmd: list[str], prompt_file: str) -> str:
    logged_cmd = list(cmd)
    if _agent_transport(config) == AgentTransport.OPENCODE and logged_cmd:
        logged_cmd[-1] = prompt_file
    return " ".join(logged_cmd)


def _normalize_opencode_model_flag(model_flag: str) -> list[str]:
    parts = model_flag.split()
    if len(parts) == _MODELED_FLAG_PARTS and parts[0] in {"-m", "--model"}:
        return [parts[0], parts[1].removeprefix("opencode/")]
    return parts


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
