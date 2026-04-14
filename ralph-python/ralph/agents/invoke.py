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

import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from loguru import logger
from tqdm import tqdm


@dataclass(frozen=True)
class InvokeOptions:
    """Options for agent invocation.

    Attributes:
        model_flag: Optional model override flag string.
        verbose: Whether to pass verbose flag to agent.
        show_progress: Whether to show tqdm progress bar.
        workspace_path: Optional path to workspace for file-change monitoring.
    """

    model_flag: str | None = None
    verbose: bool = False
    show_progress: bool = True
    workspace_path: Path | None = None


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

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
        super().__init__(f"Agent '{agent_name}' failed with code {returncode}")


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
    cmd = _build_command(config, prompt_file, model_flag=opts.model_flag, verbose=opts.verbose)
    logger.info("Invoking agent: {}", " ".join(cmd))

    monitor = _start_workspace_monitor(opts.workspace_path)

    try:
        lines_iter = _run_subprocess_and_read_lines(cmd, config, opts.show_progress)
        yield from lines_iter

        _log_workspace_completion(monitor)
        _check_process_result(cmd, config.cmd.split()[0])
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
    ) as proc:
        if proc.stdout is None:
            msg = "Failed to capture stdout"
            raise AgentInvocationError(config.cmd.split()[0], -1, msg)

        lines_iter = _read_lines_from_process(proc)
        if show_progress:
            agent_name = config.cmd.split()[0]
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
        "Agent completed. Workspace changes: {} files, {} events}",
        len(monitor.changed_files),
        monitor.event_count,
    )


def _check_process_result(cmd: list[str], agent_name: str) -> None:
    """Check subprocess return code and raise error if non-zero.

    Args:
        cmd: Command that was executed.
        agent_name: Name of the agent.

    Raises:
        AgentInvocationError: If process exited with non-zero code.
    """
    proc_result = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if proc_result.returncode == 0:
        return

    stderr = proc_result.stderr or "(unable to read stderr)"
    logger.error("Agent exited with code {}: {}", proc_result.returncode, stderr)
    raise AgentInvocationError(agent_name, proc_result.returncode, stderr)


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
    model_flag: str | None = None,
    verbose: bool = False,
) -> list[str]:
    """Build the command line for agent invocation.

    Args:
        config: Agent configuration.
        prompt_file: Path to prompt file.
        model_flag: Optional model flag override.
        verbose: Whether to include verbose flag.

    Returns:
        List of command arguments.
    """
    cmd = config.cmd.split()
    cmd.append(config.output_flag)

    if config.yolo_flag:
        cmd.append(config.yolo_flag)

    if verbose and config.verbose_flag:
        cmd.append(config.verbose_flag)

    effective_model = model_flag or config.model_flag
    if effective_model:
        cmd.extend(effective_model.split())

    cmd.append(prompt_file)

    return cmd


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
