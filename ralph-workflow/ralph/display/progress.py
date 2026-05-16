"""Progress display utilities for Ralph Workflow pipeline.

This module provides a RalphProgress context manager that wraps rich.Progress
for multi-task display, with tqdm fallback for non-TTY environments.

The RalphProgress provides:
- Overall pipeline progress (top-level task)
- Current phase progress (sub-task)
- Current agent progress (leaf task)

When running in a non-TTY environment (e.g., CI, redirected output),
tqdm is used as a fallback to ensure progress is still visible.

Example::

    ctx = make_display_context()
    with RalphProgress(ctx) as progress:
        pipeline_task = progress.add_task("Pipeline", total=100)
        with progress.phase(pipeline_task, "Planning"):
            phase_task = progress.add_task(pipeline_task, "Agent", total=50)
            # ... do work ...
            progress.update(phase_task, advance=10)
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from importlib import import_module
from io import TextIOBase
from typing import TYPE_CHECKING, ClassVar, cast

from ralph.display.progress_protocols import (
    ActivityRenderer,
    PhaseTracker,
    ProgressRenderer,
    TaskID,
    _ColumnFactory,
    _ConsoleFactory,
    _ConsoleProto,
    _GetIPython,
    _ProgressFactory,
    _ProgressProto,
    _TqdmFactory,
    _TqdmProto,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import ModuleType

    from ralph.display.context import DisplayContext


def _module_attr(module: ModuleType, attribute: str) -> object | None:
    namespace = cast("dict[str, object]", module.__dict__)
    return namespace.get(attribute)


def _load_rich_components() -> (
    tuple[
        _ConsoleFactory,
        _ProgressFactory,
        tuple[
            _ColumnFactory,
            _ColumnFactory,
            _ColumnFactory,
            _ColumnFactory,
            _ColumnFactory,
            _ColumnFactory,
            _ColumnFactory,
        ],
    ]
    | None
):
    try:
        console_module = import_module("rich.console")
        progress_module = import_module("rich.progress")
    except ImportError:
        return None

    console_factory = cast("_ConsoleFactory", _module_attr(console_module, "Console"))
    progress_factory = cast("_ProgressFactory", _module_attr(progress_module, "Progress"))
    columns = (
        cast("_ColumnFactory", _module_attr(progress_module, "SpinnerColumn")),
        cast("_ColumnFactory", _module_attr(progress_module, "TextColumn")),
        cast("_ColumnFactory", _module_attr(progress_module, "BarColumn")),
        cast("_ColumnFactory", _module_attr(progress_module, "TaskProgressColumn")),
        cast("_ColumnFactory", _module_attr(progress_module, "MofNCompleteColumn")),
        cast("_ColumnFactory", _module_attr(progress_module, "TimeElapsedColumn")),
        cast("_ColumnFactory", _module_attr(progress_module, "TimeRemainingColumn")),
    )
    return console_factory, progress_factory, columns


def _load_tqdm_factory() -> _TqdmFactory | None:
    try:
        tqdm_module = import_module("tqdm")
    except ImportError:
        return None
    return cast("_TqdmFactory", _module_attr(tqdm_module, "tqdm"))


def _load_get_ipython() -> _GetIPython | None:
    try:
        ipython_module = import_module("IPython")
    except ImportError:
        return None
    candidate = _module_attr(ipython_module, "get_ipython")
    if candidate is None or not callable(candidate):
        return None
    return cast("_GetIPython", candidate)


_RICH_AVAILABLE = _load_rich_components() is not None
_TQDM_AVAILABLE = _load_tqdm_factory() is not None
_GET_IPYTHON = _load_get_ipython()
_IPYTHON_AVAILABLE = _GET_IPYTHON is not None

__all__ = [
    "ActivityRenderer",
    "PhaseTracker",
    "ProgressRenderer",
    "RalphProgress",
    "TaskID",
    "_ColumnFactory",
    "_ConsoleFactory",
    "_ConsoleProto",
    "_GetIPython",
    "_ProgressFactory",
    "_ProgressProto",
    "_TqdmFactory",
    "_TqdmProto",
    "get_progress",
]


class RalphProgress:
    """Multi-task progress display for Ralph Workflow pipeline.

    RalphProgress manages a hierarchy of progress tasks:

    1. Pipeline-level progress (overall completion)
    2. Phase-level progress (current phase advancement)
    3. Agent-level progress (agent output lines/events)

    Uses rich.Progress when running in a TTY environment,
    falls back to tqdm when running in non-TTY (CI, redirected output).

    Note:
        RalphProgress requires a DisplayContext to be provided. The context
        is used to obtain the shared console. Instances are cached by
        console identity via _ProgressSingleton.
    """

    def __init__(self, context: DisplayContext) -> None:
        """Initialize RalphProgress.

        Args:
            context: DisplayContext providing the console for progress display.
        """
        self._context = context
        self._console: _ConsoleProto | None = None
        self._progress: _ProgressProto | None = None
        self._tqdm: _TqdmProto | None = None
        self._is_jupyter = self._check_jupyter()

    def _check_jupyter(self) -> bool:
        """Check if running in a Jupyter environment.

        Returns:
            True if in Jupyter, False otherwise.
        """
        if not _IPYTHON_AVAILABLE:
            return False
        try:
            return _GET_IPYTHON is not None and _GET_IPYTHON() is not None
        except Exception:
            return False

    def _is_tty(self) -> bool:
        """Check if output is a TTY.

        Returns:
            True if stderr is a TTY and rich is available.
        """
        if not _RICH_AVAILABLE:
            return False
        stderr = sys.stderr
        return isinstance(stderr, TextIOBase) and stderr.isatty()

    @contextmanager
    def _rich_progress(self) -> Iterator[_ProgressProto]:
        """Provide rich.Progress context manager.

        Yields:
            Configured rich Progress instance.
        """
        rich_components = _load_rich_components()
        if rich_components is None:
            raise RuntimeError("rich is unavailable")

        _, progress_factory, columns = rich_components
        (
            spinner_column,
            text_column,
            bar_column,
            task_progress_column,
            mofn_column,
            elapsed_column,
            remaining_column,
        ) = columns

        console: _ConsoleProto = cast("_ConsoleProto", self._context.console)

        progress = progress_factory(
            spinner_column(),
            text_column("[theme.cat.meta]{task.description}[/theme.cat.meta]"),
            bar_column(),
            task_progress_column(),
            mofn_column(),
            elapsed_column(),
            remaining_column(),
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
    def _tqdm_progress(self) -> Iterator[_TqdmProto]:
        """Provide tqdm context manager for non-TTY environments.

        Yields:
            Configured tqdm instance.
        """
        tqdm_factory = _load_tqdm_factory()
        if tqdm_factory is None:
            raise RuntimeError("tqdm is unavailable")

        bar = tqdm_factory(
            desc="Ralph Workflow",
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
    def phase(self, task: TaskID, phase_name: str) -> Iterator[_ProgressProto | None]:
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
            yield None

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


class _ProgressSingleton:
    """Singleton wrapper for RalphProgress, keyed by context console id.

    Note:
        Callers must NOT re-create DisplayContext per phase; they must use
        refreshed() or pass the same context. Different contexts (identified
        by console identity) yield separate RalphProgress instances.
    """

    _instances: ClassVar[dict[int, RalphProgress]] = {}

    @classmethod
    def get(cls, context: DisplayContext) -> RalphProgress:
        """Get the RalphProgress singleton for the given context.

        Args:
            context: DisplayContext whose console identity key the instance.
                Must not be None.

        Returns:
            RalphProgress instance for the given context.
        """
        key = id(context.console)
        if key not in cls._instances:
            cls._instances[key] = RalphProgress(context=context)
        return cls._instances[key]


def get_progress(context: DisplayContext) -> RalphProgress:
    """Get the default RalphProgress instance for the given context.

    Args:
        context: DisplayContext providing the console for progress display.
            Different contexts (identified by console identity) yield separate
            RalphProgress instances.

    Returns:
        RalphProgress instance for the given context.
    """
    return _ProgressSingleton.get(context)
