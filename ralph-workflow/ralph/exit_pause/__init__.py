"""Exit pause — decide whether to hold the terminal open before process exit.

Ported from ralph-workflow/src/exit_pause/io.rs.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from enum import StrEnum

import psutil


class ExitOutcome(StrEnum):
    """Possible outcomes that affect pause behavior."""

    SUCCESS = "success"
    FAILURE = "failure"
    INTERRUPTED = "interrupted"


class PauseOnExitMode(StrEnum):
    """When to pause before exiting."""

    NEVER = "never"
    ALWAYS = "always"
    AUTO = "auto"


@dataclass(frozen=True)
class LaunchContext:
    """Context about how Ralph was launched.

    Attributes:
        is_windows: Whether running on Windows.
        has_terminal_session_marker: Whether a terminal session marker is present.
        parent_process_name: Name of the parent process if detectable.
    """

    is_windows: bool
    has_terminal_session_marker: bool
    parent_process_name: str | None


TERMINAL_MARKERS: list[str] = [
    "WT_SESSION",
    "TERM",
    "MSYSTEM",
    "ConEmuPID",
    "ALACRITTY_LOG",
    "TERM_PROGRAM",
    "VSCODE_GIT_IPC_HANDLE",
]


def _has_terminal_session_marker() -> bool:
    """Check if any terminal session marker environment variable is set."""
    for marker in TERMINAL_MARKERS:
        value = os.environ.get(marker)
        if value is not None and value.strip():
            return True
    return False


def _normalize_process_name(name: str) -> str:
    """Normalize a process name for comparison."""
    normalized = name.strip().lower()
    if "." in normalized:
        ext = normalized.rsplit(".", 1)[-1]
        if ext == "exe":
            return normalized
        return f"{normalized}.exe"
    return f"{normalized}.exe"


def _is_probably_standalone_windows_launch(ctx: LaunchContext) -> bool:
    """Check if this looks like a standalone Windows launch (e.g., from Explorer)."""
    if not ctx.is_windows or ctx.has_terminal_session_marker:
        return False
    if ctx.parent_process_name is None:
        return False
    return _normalize_process_name(ctx.parent_process_name) == "explorer.exe"


def should_pause_before_exit(
    mode: PauseOnExitMode,
    outcome: ExitOutcome,
    launch_context: LaunchContext,
) -> bool:
    """Determine if we should pause before exiting.

    Args:
        mode: The pause mode setting.
        outcome: The exit outcome (success/failure/interrupted).
        launch_context: Information about how Ralph was launched.

    Returns:
        True if we should pause, False otherwise.
    """
    if mode == PauseOnExitMode.NEVER:
        return False
    if mode == PauseOnExitMode.ALWAYS:
        return True
    # AUTO mode: pause on failure if launched standalone on Windows
    return outcome == ExitOutcome.FAILURE and _is_probably_standalone_windows_launch(launch_context)


def detect_launch_context() -> LaunchContext:
    """Detect the launch context for the current process.

    Returns:
        LaunchContext with information about how Ralph was launched.
    """
    is_windows = sys.platform == "win32" or os.name == "nt"
    has_marker = _has_terminal_session_marker()
    parent_name: str | None = None

    if is_windows:
        parent_name = _detect_parent_on_windows()

    return LaunchContext(
        is_windows=is_windows,
        has_terminal_session_marker=has_marker,
        parent_process_name=parent_name,
    )


def _detect_parent_on_windows() -> str | None:
    """Detect parent process name using psutil."""
    try:
        parent = psutil.Process(os.getpid()).parent()
        if parent is None:
            return None
        return parent.name()
    except Exception:
        return None


def exit_pause(mode: PauseOnExitMode = PauseOnExitMode.AUTO) -> None:
    """Pause before exit if conditions require it.

    On Windows standalone launches (e.g., double-clicked .exe), it's helpful
    to pause so the user can read any error messages before the window closes.

    Args:
        mode: The pause mode setting (default: AUTO).
    """
    ctx = detect_launch_context()
    if should_pause_before_exit(mode, ExitOutcome.FAILURE, ctx):
        input("Press Enter to exit...")


def exit_with_sigint_code() -> None:
    """Exit with SIGINT exit code (130).

    Called when the pipeline was interrupted by Ctrl+C and all cleanup
    has completed.
    """
    sys.exit(130)


__all__ = [
    "ExitOutcome",
    "LaunchContext",
    "PauseOnExitMode",
    "detect_launch_context",
    "exit_pause",
    "exit_with_sigint_code",
    "should_pause_before_exit",
]
