"""Parallel display skeleton with TTY/CI mode detection.

Provides ParallelDisplay, a context manager that routes output to either a
live dashboard (rich, TTY) or plain line-by-line output (CI, dumb terminals).
Rendering logic is filled in by subsequent tasks (T24 for emit, T25 for
set_status).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import TracebackType

    from rich.console import Console

    from ralph.pipeline.worker_state import WorkerStatus

NARROW_THRESHOLD: int = 60


def detect_mode(
    console: Console,
    env: Mapping[str, str],
) -> Literal["dashboard", "lines"]:
    """Detect whether to use dashboard or lines display mode.

    Returns "lines" when any of the following hold:
    - env["CI"] is a non-empty truthy string
    - "NO_COLOR" key is present in env (any value, including empty)
    - env["TERM"] == "dumb"
    - console.is_terminal is False
    - console.width <= NARROW_THRESHOLD

    Returns "dashboard" otherwise.
    """
    if env.get("CI"):
        return "lines"
    if "NO_COLOR" in env:
        return "lines"
    if env.get("TERM") == "dumb":
        return "lines"
    if not console.is_terminal:
        return "lines"
    if console.width <= NARROW_THRESHOLD:
        return "lines"
    return "dashboard"


class ParallelDisplay:
    """Display manager for parallel pipeline workers.

    Detects at construction time whether to run in "dashboard" mode (rich live
    rendering, TTY) or "lines" mode (plain line-by-line output, CI/dumb).
    The mode is frozen after __init__ and cannot be changed.

    Args:
        console: Rich Console instance used for rendering and TTY detection.
        env: Environment mapping used for mode detection.  Defaults to
            os.environ when None.
    """

    __slots__ = ("_console", "_mode")

    def __init__(
        self,
        console: Console,
        env: Mapping[str, str] | None = None,
    ) -> None:
        resolved_env: Mapping[str, str] = os.environ if env is None else env
        self._console = console
        self._mode: Literal["dashboard", "lines"] = detect_mode(console, resolved_env)

    @property
    def mode(self) -> Literal["dashboard", "lines"]:
        """Display mode, frozen after construction."""
        return self._mode

    def start(self) -> None:
        """Start the display (stub — rendering wired in T24)."""

    def stop(self) -> None:
        """Stop the display (stub — rendering wired in T24)."""

    def emit(self, unit_id: str | None, line: str) -> None:
        """Emit an output line from a worker (stub — wired in T24).

        Args:
            unit_id: Identifier of the originating work unit, or None for
                unattributed output.
            line: The sanitized text line to display.
        """

    def set_status(self, unit_id: str, status: WorkerStatus) -> None:
        """Update the status of a worker (stub — wired in T25).

        Args:
            unit_id: Identifier of the work unit.
            status: New WorkerStatus value.
        """

    def __enter__(self) -> ParallelDisplay:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.stop()


__all__ = ["NARROW_THRESHOLD", "ParallelDisplay", "detect_mode"]
