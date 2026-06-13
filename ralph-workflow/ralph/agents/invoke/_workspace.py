"""Workspace monitoring for file changes during agent execution."""

from __future__ import annotations

import importlib
import time
from typing import TYPE_CHECKING, Protocol, cast

from loguru import logger

from ralph.agents.invoke._has_src_path import _HasSrcPath

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

if TYPE_CHECKING:

    class _HasStop(Protocol):
        """Protocol for watchdog Observer-like objects that have a stop method."""

        def stop(self) -> None: ...
        def join(self, _timeout: float | None = None) -> None: ...

    class _ObserverProtocol(_HasStop, Protocol):
        """Protocol for watchdog Observer-like objects used by this module."""

        def schedule(self, _event_handler: object, path: str, **_kwargs: object) -> None: ...
        def start(self) -> None: ...

    class _WatchdogObserversModule(Protocol):
        """Typed accessor for the optional watchdog.observers module."""

        Observer: type[_ObserverProtocol]


_MAX_WORKSPACE_CHANGED_FILES = 512


def _make_change_tracker(monitor: WorkspaceMonitor) -> object:
    class _ChangeTrackerHandler:
        def dispatch(self, event: object) -> None:
            self.on_any_event(event)

        def on_any_event(self, event: object) -> None:
            if isinstance(event, _HasSrcPath):
                monitor.record_event(event.src_path)

    return _ChangeTrackerHandler()


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


class WorkspaceMonitor:
    """Monitors workspace directory for file changes during agent execution.

    This allows the pipeline to detect when an agent has completed significant
    work by watching for file modifications in the workspace.
    """

    def __init__(
        self,
        workspace_path: Path,
        *,
        now: Callable[[], float] | None = None,
        on_event: Callable[[], None] | None = None,
    ) -> None:
        """Initialize workspace monitor.

        Args:
            workspace_path: Path to the workspace directory to monitor.
            now: Optional monotonic-clock callable. Defaults to ``time.monotonic``
                for production. Tests inject a FakeClock-bound callable to drive
                ``last_event_at`` deterministically (see tests in
                tests/agents/test_idle_watchdog_3.py::test_workspace_monitor_records_last_event_at).
            on_event: Optional callable invoked at the end of ``record_event``
                after the timestamp and counter are updated. Production readers
                bind this to ``watchdog.record_workspace_event`` (via
                ``set_on_event`` after the watchdog is constructed) so the
                activity-aware verdict can defer ``NO_OUTPUT_DEADLINE`` while
                the workspace is changing. Exceptions raised by ``on_event`` are
                swallowed so a buggy callback cannot break the file-event path.
        """
        self._workspace = workspace_path
        self._observer: _HasStop | None = None
        self._event_count = 0
        self._seen_files: dict[str, None] = {}
        self._now: Callable[[], float] = now if now is not None else time.monotonic
        self._last_event_at: float | None = None
        self._on_event: Callable[[], None] | None = on_event

    def start(self) -> None:
        """Start monitoring the workspace for file changes."""
        observer = _create_watchdog_observer()
        if observer is None:
            return

        handler = _make_change_tracker(self)
        self._observer = observer
        self._observer.schedule(handler, str(self._workspace), recursive=True)
        self._observer.start()
        logger.debug("Started workspace monitoring: {}", self._workspace)

    def record_event(self, src_path: str) -> None:
        """Record a file change event.

        Updates ``last_event_at`` to the current monotonic-clock value
        (production: ``time.monotonic``; tests: the injected fake clock).
        The watchdog's per-channel evidence surface consumes this timestamp
        via the ``last_workspace_event_at`` field on CorroborationSnapshot
        so a workspace-event channel is fresh exactly as long as the
        production clock is recent.

        When an ``on_event`` callback has been registered (via the
        constructor or ``set_on_event``), it is invoked AFTER the
        timestamp and counter are updated so the watchdog observes a
        fully-consistent state. The callback is invoked in a
        ``try/except`` so a buggy callback cannot break the
        file-event path; the failure is logged at DEBUG.

        Args:
            src_path: Path to the changed file.
        """
        self._seen_files.pop(src_path, None)
        self._seen_files[src_path] = None
        while len(self._seen_files) > _MAX_WORKSPACE_CHANGED_FILES:
            oldest = next(iter(self._seen_files))
            del self._seen_files[oldest]
        self._event_count += 1
        self._last_event_at = self._now()
        if self._on_event is not None:
            try:
                self._on_event()
            except Exception:
                logger.opt(exception=True).debug(
                    "workspace monitor: on_event callback raised (suppressed)"
                )

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
    def last_event_at(self) -> float | None:
        """Monotonic-clock timestamp of the most recent file change event.

        Returns None when no event has been observed since the monitor was
        constructed (or since the last ``reset_last_event_at`` call). The
        watchdog's per-channel evidence surface consumes this value via
        the ``last_workspace_event_at`` field on ``CorroborationSnapshot``;
        a fresh workspace channel defers the NO_OUTPUT_DEADLINE verdict
        while the channel age is below ``activity_evidence_ttl_seconds``.
        """
        return self._last_event_at

    def reset_last_event_at(self) -> None:
        """Reset ``last_event_at`` (and the event counter) to a clean state.

        Intended for test isolation: a long-lived ``WorkspaceMonitor`` in
        a test fixture may have observed events from a prior case; calling
        this clears the timestamp so the next ``record_event`` produces a
        fresh baseline.
        """
        self._last_event_at = None
        self._event_count = 0
        self._seen_files.clear()

    def set_on_event(self, on_event: Callable[[], None] | None) -> None:
        """Register (or clear) the per-event callback invoked at the end of
        ``record_event``.

        Production readers construct the ``WorkspaceMonitor`` BEFORE the
        per-run watchdog is created (the monitor is built in
        ``invoke_agent`` while the watchdog lives inside the reader's
        ``read_lines`` generator), so the constructor cannot bind the
        watchdog's ``record_workspace_event`` directly. The reader
        registers the callback here, immediately after the watchdog is
        created, so every subsequent file change is visible to the
        activity-aware verdict as workspace channel evidence.

        Pass ``None`` to clear the callback (e.g. when the per-run
        watchdog is torn down at run end).

        Args:
            on_event: Callable invoked with no arguments at the end of
                ``record_event``, after the timestamp and counter are
                updated. Exceptions raised by the callback are
                suppressed by ``record_event`` so a buggy callback
                cannot break the file-event path.
        """
        self._on_event = on_event

    @property
    def changed_files(self) -> set[str]:
        """Set of file paths that changed during monitoring."""
        return set(self._seen_files)
