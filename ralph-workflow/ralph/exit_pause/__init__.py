
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

import psutil

from ralph.exit_pause.exit_outcome import ExitOutcome
from ralph.exit_pause.pause_on_exit_mode import PauseOnExitMode

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True)
class LaunchContext:

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


def _has_terminal_session_marker(env: Mapping[str, str]) -> bool:
    for marker in TERMINAL_MARKERS:
        value = env.get(marker)
        if value is not None and value.strip():
            return True
    return False


def _normalize_process_name(name: str) -> str:
    normalized = name.strip().lower()
    if "." in normalized:
        ext = normalized.rsplit(".", 1)[-1]
        if ext == "exe":
            return normalized
        return f"{normalized}.exe"
    return f"{normalized}.exe"


def _is_probably_standalone_windows_launch(ctx: LaunchContext) -> bool:
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
    if mode == PauseOnExitMode.NEVER:
        return False
    if mode == PauseOnExitMode.ALWAYS:
        return True
    return outcome == ExitOutcome.FAILURE and _is_probably_standalone_windows_launch(launch_context)


def detect_launch_context(*, env: Mapping[str, str] | None = None) -> LaunchContext:
    is_windows = sys.platform == "win32" or os.name == "nt"
    env_map = os.environ if env is None else env
    has_marker = _has_terminal_session_marker(env_map)
    parent_name: str | None = None

    if is_windows:
        parent_name = _detect_parent_on_windows()

    return LaunchContext(
        is_windows=is_windows,
        has_terminal_session_marker=has_marker,
        parent_process_name=parent_name,
    )


def _detect_parent_on_windows() -> str | None:
    try:
        parent = psutil.Process(os.getpid()).parent()
        if parent is None:
            return None
        return parent.name()
    except Exception:
        return None


def exit_pause(mode: PauseOnExitMode = PauseOnExitMode.AUTO) -> None:
    ctx = detect_launch_context()
    if should_pause_before_exit(mode, ExitOutcome.FAILURE, ctx):
        input("Press Enter to exit...")


def exit_with_sigint_code() -> None:
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
