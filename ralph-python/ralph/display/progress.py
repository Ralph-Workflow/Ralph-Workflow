"""Progress display utilities for Ralph pipeline.

This module provides a RalphProgress context manager that wraps rich.Progress
for multi-task display, with tqdm fallback for non-TTY environments.

The RalphProgress provides:
- Overall pipeline progress (top-level task)
- Current phase progress (sub-task)
- Current agent progress (leaf task)

When running in a non-TTY environment (e.g., CI, redirected output),
tqdm is used as a fallback to ensure progress is still visible.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

if TYPE_CHECKING:
    from rich.progress import Progress

try:
    from rich.console import Console
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TaskID,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
    )

    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False

try:
    from tqdm import tqdm

    _TQDM_AVAILABLE = True
except ImportError:
    _TQDM_AVAILABLE = False

try:
    from IPython import get_ipython

    _IPYTHON_AVAILABLE = get_ipython is not None
except ImportError:
    _IPYTHON_AVAILABLE = False


class RalphProgress:
    """Multi-task progress display for Ralph pipeline.

    RalphProgress manages a hierarchy of progress tasks:
    1. Pipeline-level progress (overall completion)
    2. Phase-level progress (current phase advancement)
    3. Agent-level progress (agent output lines/events)

    Uses rich.Progress when running in a TTY environment,
    falls back to tqdm when running in non-TTY (CI, redirected output).

    Example:
        with RalphProgress() as progress:
            pipeline_task = progress.add_task("Pipeline", total=100)
            with progress.phase(pipeline_task, "Planning"):
                phase_task = progress.add_task(pipeline_task, "Agent", total=50)
                # ... do work ...
                progress.update(phase_task, advance=10)
    """

    def __init__(self) -> None:
        """Initialize RalphProgress."""
        self._console: Console | None = None
        self._progress: Progress | None = None
        self._tqdm: tqdm | None = None
        self._is_jupyter = self._check_jupyter()

    def _check_jupyter(self) -> bool:
        """Check if running in a Jupyter environment.

        Returns:
            True if in Jupyter, False otherwise.
        """
        if not _IPYTHON_AVAILABLE:
            return False
        try:
            # get_ipython is already imported at module level
            return get_ipython is not None and get_ipython() is not None
        except Exception:
            return False

    def _is_tty(self) -> bool:
        """Check if output is a TTY.

        Returns:
            True if stderr is a TTY and rich is available.
        """
        if not _RICH_AVAILABLE:
            return False
        return sys.stderr.isatty()

    @contextmanager
    def _rich_progress(self) -> Iterator[Progress]:
        """Provide rich.Progress context manager.

        Yields:
            Configured rich Progress instance.
        """
        console = Console(stderr=True)
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description}[/bold cyan]"),
            BarColumn(),
            TaskProgressColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=False,
            auto_refresh=True,
        )
        self._console = console
        self._progress = progress

        with progress:
            yield progress

        self._progress = None
        self._console = None

    @contextmanager
    def _tqdm_progress(self) -> Iterator[tqdm]:
        """Provide tqdm context manager for non-TTY environments.

        Yields:
            Configured tqdm instance.
        """
        bar = tqdm(
            desc="Ralph",
            unit="iter",
            file=sys.stderr,
            leave=True,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]",
        )
        self._tqdm = bar

        try:
            yield bar
        finally:
            bar.close()
            self._tqdm = None

    def __enter__(self) -> RalphProgress:
        """Enter the progress context.

        Returns:
            Self for use as context manager.
        """
        return self

    def __exit__(self, *args: object) -> None:
        """Exit the progress context."""
        # Cleanup handled by context managers
        pass

    @contextmanager
    def phase(self, task: TaskID, phase_name: str) -> Iterator[Progress]:
        """Context manager for a phase within a pipeline task.

        Args:
            task: Parent pipeline task ID.
            phase_name: Name of the current phase.

        Yields:
            Progress for adding phase subtasks.
        """
        if self._progress is not None:
            self._progress.add_task(
                f"  {phase_name}",
                parent=task,
                total=None,
            )
            try:
                yield self._progress
            finally:
                pass  # Let parent manage lifecycle
        else:
            yield None  # type: ignore[misc]

    def add_task(
        self,
        description: str,
        total: int | None = 100,
        completed: int = 0,
        parent: TaskID | None = None,
    ) -> TaskID:
        """Add a new progress task.

        Args:
            description: Task description.
            total: Total units of work.
            completed: Initial completed units.
            parent: Parent task ID for nested tasks.

        Returns:
            Task ID for use in update/remove.
        """
        if self._progress is not None:
            return self._progress.add_task(
                description,
                total=total,
                completed=completed,
                parent=parent,
            )
        else:
            # Return dummy ID for non-rich mode
            return TaskID(0)

    def update(
        self,
        task: TaskID,
        completed: int | None = None,
        advance: int = 0,
        description: str | None = None,
    ) -> None:
        """Update a progress task.

        Args:
            task: Task ID to update.
            completed: New completed value (absolute).
            advance: Amount to advance (relative).
            description: New description.
        """
        if self._progress is not None:
            self._progress.update(
                task_id=task,
                completed=completed,
                advance=advance if advance else None,
                description=description,
            )
        elif self._tqdm is not None:
            if advance:
                self._tqdm.update(advance)
            if completed is not None:
                self._tqdm.n = completed
                self._tqdm.refresh()


# Module-level convenience instance
_default_progress: RalphProgress | None = None


class _ProgressSingleton:
    """Singleton wrapper for RalphProgress."""

    _instance: RalphProgress | None = None

    @classmethod
    def get(cls) -> RalphProgress:
        """Get the singleton RalphProgress instance."""
        if cls._instance is None:
            cls._instance = RalphProgress()
        return cls._instance


def get_progress() -> RalphProgress:
    """Get the default RalphProgress instance.

    Returns:
        RalphProgress singleton.
    """
    return _ProgressSingleton.get()
