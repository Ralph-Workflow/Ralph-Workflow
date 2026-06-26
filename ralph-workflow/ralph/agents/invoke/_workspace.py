"""Workspace monitoring for file changes during agent execution."""

from __future__ import annotations

import importlib
import inspect
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, cast

from loguru import logger

from ralph.agents.idle_watchdog._workspace_change_kind import WorkspaceChangeKind
from ralph.agents.invoke._has_src_path import _HasSrcPath

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.agents.invoke._workspace_change_classifier import (
        WorkspaceChangeClassifier,
    )

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
_VALID_CALLBACK_ARITIES: frozenset[int] = frozenset({0, 2})
_TWO_ARG_ARITY: int = 2


#: Union of the two valid on_event callback signatures. A callback
#: with no required positional args (the legacy 0-arg binding) is
#: accepted for backward compatibility; a callback with exactly 2
#: required positional args (the production 2-arg binding) carries
#: ``(kind, weight)`` so the watchdog's per-kind counter receives
#: real classifications. The ``__post_init__`` arity check rejects
#: any other arity at construction time.
WorkspaceEventCallback = Callable[[], None] | Callable[[WorkspaceChangeKind, float], None]


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


def _callback_arity(callback: WorkspaceEventCallback) -> int:
    """Return the number of required positional parameters of ``callback``.

    Used by ``WorkspaceMonitor.__post_init__`` to enforce the 0-arg
    or 2-arg contract. ``inspect.signature`` already follows
    ``functools.partial`` and ``functools.wraps`` chains, and
    automatically excludes the bound ``self`` parameter for bound
    methods, so the returned ``signature.parameters`` map is
    authoritative for the effective positional arity. A callback
    with ``*args`` or ``**kwargs`` has a non-finite arity; the
    classifier counts the explicit positional slots before ``*args``
    and treats the result as the effective arity.

    Returns:
        Number of required positional parameters as observed by the
        caller (excluding ``self`` for bound methods).
    """
    try:
        signature_obj: inspect.Signature = inspect.signature(callback)
    except (TypeError, ValueError):
        msg = (
            f"WorkspaceMonitor on_event callback has an uninspectable signature;"
            f" expected 0 or 2 required positional args, got callback of type"
            f" {type(callback).__name__}"
        )
        raise ValueError(msg) from None
    can_bind_zero = _can_bind_n(signature_obj, 0)
    can_bind_two = _can_bind_n(signature_obj, 2)
    if can_bind_zero and not can_bind_two:
        return 0
    if can_bind_two and not can_bind_zero:
        return 2
    msg = (
        f"WorkspaceMonitor on_event callback has the wrong arity;"
        f" expected exactly 0 or 2 required positional args, got"
        f" callback of type {type(callback).__name__}"
    )
    raise ValueError(msg)


def _can_bind_n(signature_obj: inspect.Signature, n: int) -> bool:
    """Return True iff ``signature_obj`` accepts exactly ``n`` positional args.

    A variadic-only signature (e.g. ``*args, **kwargs``) accepts any
    number of args, so both ``n=0`` and ``n=2`` return True. The
    arity check in ``_callback_arity`` rejects signatures where
    both bind successfully, so a variadic-only callback is not
    mistakenly classified as 0-arg or 2-arg.

    Used to avoid touching ``Parameter.kind`` (which is typed as
    ``Any`` in the upstream typeshed stub) and the
    ``Parameter.empty`` sentinel (also ``Any``-typed) so the
    mypy ``disallow_any_expr`` check does not flag the
    ``inspect.Parameter``-typed expressions.
    """
    args: tuple[object, ...] = tuple(object() for _ in range(n))
    try:
        signature_obj.bind(*args)
    except TypeError:
        return False
    return True


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
        on_event: WorkspaceEventCallback | None = None,
        classifier: WorkspaceChangeClassifier | None = None,
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

                Two arities are accepted: the legacy 0-arg form (the
                watchdog's ``record_workspace_event`` bound method, which
                receives only ``now`` via the watchdog's clock) and the
                production 2-arg form (a small lambda that forwards
                ``(kind, weight)`` so the watchdog's per-kind counter
                receives real classifications). A 1-arg or 3+-arg callback
                is rejected at construction time with a clear ValueError
                naming the offending arity.

                The callback is invoked in a ``try/except`` so a buggy
                callback cannot break the file-event path; the failure
                is logged at DEBUG.
            classifier: Optional ``WorkspaceChangeClassifier`` used to
                classify each event into a ``WorkspaceChangeKind`` and a
                binary weight. When omitted (or ``None``), every event
                is classified as ``OTHER`` with weight ``1.0`` (the legacy
                behavior: every file change counts as activity). When
                provided, events with weight ``0.0`` are dropped
                before ``on_event`` is invoked: the timestamp and
                counter are NOT updated and the callback is NOT
                invoked. Events with weight ``1.0`` are passed to the
                callback together with their ``(kind, weight)`` tuple
                when the callback accepts 2 args.
        """
        self._workspace = workspace_path
        self._observer: _HasStop | None = None
        self._event_count = 0
        self._seen_files: dict[str, None] = {}  # bounded-accumulator-ok: bounded
        self._now: Callable[[], float] = now if now is not None else time.monotonic
        self._last_event_at: float | None = None
        if on_event is not None:
            arity = _callback_arity(on_event)
            if arity not in _VALID_CALLBACK_ARITIES:
                msg = (
                    f"WorkspaceMonitor on_event callback has arity {arity};"
                    f" expected 0 (legacy) or 2 (production-style forwarding of"
                    f" (kind, weight))."
                )
                raise ValueError(msg)
        self._on_event: WorkspaceEventCallback | None = on_event
        self._classifier: WorkspaceChangeClassifier | None = classifier

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

        Classifies the event via the configured ``WorkspaceChangeClassifier``
        (or the legacy ``OTHER / 1.0`` fallback when no classifier is
        configured). Events with weight ``0.0`` are dropped without
        updating ``last_event_at``, the counter, or invoking
        ``on_event``. Events with weight ``1.0`` update the timestamp
        and counter and invoke ``on_event`` with the ``(kind, weight)``
        pair when the callback accepts 2 args.

        The watchdog's per-channel evidence surface consumes this
        timestamp via the ``last_workspace_event_at`` field on
        ``CorroborationSnapshot`` so a workspace-event channel is
        fresh exactly as long as the production clock is recent.

        When an ``on_event`` callback has been registered (via the
        constructor or ``set_on_event``), it is invoked AFTER the
        timestamp and counter are updated so the watchdog observes a
        fully-consistent state. The callback is invoked in a
        ``try/except`` so a buggy callback cannot break the
        file-event path; the failure is logged at DEBUG.

        Args:
            src_path: Path to the changed file.
        """
        kind, weight = self.classify_path(src_path)
        if weight == 0.0:
            return
        self._seen_files.pop(src_path, None)
        self._seen_files[src_path] = None
        while len(self._seen_files) > _MAX_WORKSPACE_CHANGED_FILES:
            oldest = next(iter(self._seen_files))
            del self._seen_files[oldest]
        self._event_count += 1
        self._last_event_at = self._now()
        if self._on_event is not None:
            try:
                arity = _callback_arity(self._on_event)
                if arity == _TWO_ARG_ARITY:
                    two_arg = cast(
                        "Callable[[WorkspaceChangeKind, float], None]",
                        self._on_event,
                    )
                    two_arg(kind, weight)
                else:
                    zero_arg = cast("Callable[[], None]", self._on_event)
                    zero_arg()
            except Exception:
                logger.opt(exception=True).debug(
                    "workspace monitor: on_event callback raised (suppressed)"
                )

    def classify_path(self, src_path: str) -> tuple[WorkspaceChangeKind, float]:
        """Classify a single workspace path via the configured classifier.

        When no classifier is configured, every path is classified as
        ``OTHER`` with weight ``1.0`` (the legacy behavior: every
        file change counts as activity). This helper is the
        canonical seam for tests and dry-run checks that want to
        inspect the classifier output without recording an event.
        """
        if self._classifier is None:
            return WorkspaceChangeKind.OTHER, 1.0
        return self._classifier.classify(src_path)

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

    def set_on_event(self, on_event: WorkspaceEventCallback | None) -> None:
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
                ``record_event`` (legacy 0-arg form) or with
                ``(kind, weight)`` (production 2-arg form) after the
                timestamp and counter are updated. Exceptions raised
                by the callback are suppressed by ``record_event``
                so a buggy callback cannot break the file-event path.
        """
        if on_event is not None:
            arity = _callback_arity(on_event)
            if arity not in _VALID_CALLBACK_ARITIES:
                msg = (
                    f"WorkspaceMonitor on_event callback has arity {arity};"
                    f" expected 0 (legacy) or 2 (production-style forwarding of"
                    f" (kind, weight))."
                )
                raise ValueError(msg)
        self._on_event = on_event

    @property
    def changed_files(self) -> set[str]:
        """Set of file paths that changed during monitoring."""
        return set(self._seen_files)
