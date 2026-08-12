"""Workspace monitoring for file changes during agent execution."""

from __future__ import annotations

import errno
import importlib
import inspect
import os
import sys
import threading
import time
import weakref
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Protocol, cast, runtime_checkable

from loguru import logger

from ralph.agents.idle_watchdog._workspace_change_kind import WorkspaceChangeKind
from ralph.agents.invoke._has_src_path import _HasSrcPath
from ralph.workspace._cross_process_watch_lock import CrossProcessWatchLock
from ralph.workspace.awareness import awareness_for_workspace, release_workspace_awareness

if TYPE_CHECKING:
    from ralph.agents.invoke._workspace_change_classifier import WorkspaceChangeClassifier

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


@runtime_checkable
class _HasDestPath(Protocol):
    """Structural watchdog event protocol for move destinations."""

    dest_path: str


class _DirectoryCounter(Protocol):
    """Structural contract for bounded recursive directory counters."""

    def __call__(self, workspace: Path, *, cap: int) -> int | None: ...


class _HandlerWithDispatch(Protocol):
    """Structural type of the per-monitor watchdog handler.

    ``_make_change_tracker`` returns a class with a public
    ``dispatch(event)`` method; ``WorkspaceMonitor.dispatch_event``
    routes test-supplied events through that method. Defined as a
    Protocol so the ``cast`` in ``dispatch_event`` does not need an
    ``attr-defined`` suppression (test files must have zero
    suppressions).
    """

    def dispatch(self, event: object) -> None: ...


def _is_within_workspace(workspace: Path, src_path: str) -> bool:
    """Return whether a delivered event path remains inside the monitor root."""
    try:
        Path(src_path).resolve(strict=False).relative_to(workspace.resolve(strict=False))
    except ValueError:
        return False
    return True


def _make_change_tracker(workspace: Path, key: str) -> object:
    class _ChangeTrackerHandler:
        def dispatch(self, event: object) -> None:
            self.on_any_event(event)

        def on_any_event(self, event: object) -> None:
            if isinstance(event, _HasSrcPath) and _is_within_workspace(workspace, event.src_path):
                WorkspaceMonitor._record_event_for_key(key, event.src_path)
                return
            if isinstance(event, _HasDestPath) and _is_within_workspace(workspace, event.dest_path):
                WorkspaceMonitor._record_event_for_key(key, event.dest_path)

    return _ChangeTrackerHandler()


def _read_inotify_watch_limit() -> int | None:
    """Return Linux's per-real-user inotify-watch ceiling when available."""
    if sys.platform == "linux":
        try:
            return int(Path("/proc/sys/fs/inotify/max_user_watches").read_text().strip())  # filesystem-read-ok: Linux kernel sysctl, not workspace content
        except OSError:
            return None
    return None


def _read_inotify_watch_user_total() -> int | None:
    """Return the current real user's live inotify-watch count on Linux."""
    if sys.platform == "linux":
        try:
            user_id = os.getuid()
            watch_total = 0
            for process in Path("/proc").iterdir():  # filesystem-read-ok: Linux /proc kernel tree, not workspace content
                if not process.name.isdigit() or process.stat().st_uid != user_id:
                    continue
                for fdinfo in (process / "fdinfo").iterdir():
                    with fdinfo.open() as stream:
                        watch_total += sum(line.startswith("wd:") for line in stream)
        except OSError:
            return None
        return watch_total
    return None


def _count_watchable_directories(workspace: Path, cap: int) -> int | None:
    """Count workspace directories, returning None once ``cap`` is reached."""
    directory_count = 0
    try:
        for _root, directories, _files in os.walk(workspace):  # filesystem-read-ok: bounded capacity counter before Workspace exists
            directory_count += 1
            if directory_count >= cap:
                return None
            directories.sort()
    except OSError:
        return None
    return directory_count


def _watch_capacity_is_predicted(
    workspace: Path,
    host_budget: int | None,
    directory_counter: _DirectoryCounter | None,
    live_watch_total: int | None,
) -> bool:
    """Return whether another recursive workspace watch would exhaust the budget."""
    budget = host_budget if host_budget is not None else _read_inotify_watch_limit()
    if budget is None:
        return False
    wd_total = (
        live_watch_total
        if live_watch_total is not None
        else (_read_inotify_watch_user_total() or 0)
    )
    counter = directory_counter or _count_watchable_directories
    counted_directories = counter(workspace, cap=budget + 1)
    workspace_dir_count = budget + 1 if counted_directories is None else counted_directories
    return wd_total + workspace_dir_count >= budget


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


class _SharedWorkspaceWatch:
    """One observer plus weak leases for a canonical workspace root.

    ``cross_process_owner_id`` tracks the advisory-lock owner so the
    cross-process lock is released exactly when the shared observer is
    torn down (the final in-process lease), not when any single lease
    stops (S-10 / DA-001 / DA-009).
    """

    def __init__(
        self,
        observer: _ObserverProtocol,
        handler: object,
        *,
        cross_process_owner_id: str | None = None,
    ) -> None:
        self.observer = observer
        self.handler = handler
        self.monitors: weakref.WeakSet[WorkspaceMonitor] = weakref.WeakSet()
        self.cross_process_owner_id = cross_process_owner_id


def _shared_workspace_key(workspace: Path) -> str:
    """Return the lexical root key without probing a possibly absent workspace."""
    return str(workspace.absolute())


def _current_status() -> dict[str, object]:
    """Return the status for an active event observer."""
    return {
        "mode": "watch",
        "freshness": "current",
        "cause": None,
        "automatic_recovery": False,
        "safe_next_action": "None required.",
    }


def _live_fallback_status(cause: str) -> dict[str, object]:
    """Return the bounded, explicit status used without a host observer."""
    return {
        "mode": "live_fallback",
        "freshness": "live_fallback",
        "cause": cause,
        "automatic_recovery": True,
        "safe_next_action": "Ralph will retry observation on the next workspace lease.",
    }


class WorkspaceMonitor:
    """Monitors workspace directory for file changes during agent execution.

    This allows the pipeline to detect when an agent has completed significant
    work by watching for file modifications in the workspace.
    """

    _shared_watches: ClassVar[
        dict[str, _SharedWorkspaceWatch]
    ] = {}  # bounded-accumulator-ok: leases remove the final workspace entry
    _shared_lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def shared_watch_snapshot(cls) -> list[tuple[str, tuple[WorkspaceMonitor, ...]]]:
        """Read-only snapshot of the shared lease table for diagnostics.

        Returns sorted ``(scope_key, monitors)`` pairs so operator-facing
        surfaces (``ralph workspace-health``) can report active
        observation without touching private class state.
        """
        with cls._shared_lock:
            return [
                (key, tuple(sorted(shared.monitors, key=id)))
                for key, shared in sorted(cls._shared_watches.items())
            ]

    def __init__(
        self,
        workspace_path: Path,
        *,
        now: Callable[[], float] | None = None,
        on_event: WorkspaceEventCallback | None = None,
        classifier: WorkspaceChangeClassifier | None = None,
        host_budget: int | None = None,
        directory_counter: _DirectoryCounter | None = None,
        live_watch_total: int | None = None,
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
            host_budget: Explicit per-user inotify-watch ceiling. ``None``
                detects the Linux host value when monitoring starts.
            directory_counter: Optional bounded workspace-directory counter.
                ``None`` uses the Linux-safe production counter at start time.
            live_watch_total: Explicit current-user inotify-watch count.
                ``None`` detects the Linux host value when monitoring starts.
        """
        self._workspace = workspace_path
        self._observer: _ObserverProtocol | None = None
        self._shared_key: str | None = None
        self._started = False
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
        self._host_budget = host_budget
        self._directory_counter = directory_counter
        self._live_watch_total = live_watch_total
        self._handler: object | None = None
        self._cross_process_owner_id: str | None = None
        self._awareness_status: dict[str, object] = _live_fallback_status("unavailable")

    def start(self) -> None:
        """Start monitoring the workspace for file changes.

        Schedules exactly ONE recursive watchdog watch on the workspace
        root. Repeated starts while that watch is active preserve it instead
        of tearing it down or registering an overlapping subscription. A single
        recursive root watch is the minimal-stream option
        for macOS fseventsd: watchdog's fsevents backend is OS-recursive
        (see watchdog/observers/fsevents.py lines 85-87 -- ``"fsevents
        defaults to be recursive, so if the watch was meant to be
        non-recursive then we need to drop all the events here"``), so
        non-recursive subscriptions cannot reduce fseventsd delivery
        and only multiply overlapping streams. Activity-counting
        correctness is preserved by ``record_event``'s ``weight == 0.0``
        classify-drop backstop, which is independent of how many
        watchdog watches are scheduled.
        """
        if self._started:
            return

        key = _shared_workspace_key(self._workspace)
        with self._shared_lock:
            if self._attach_existing_watch(key):
                return
            if self._watch_capacity_is_predicted():
                self._activate_live_fallback("watch_capacity_predicted")
                logger.warning(
                    "Workspace monitoring unavailable: predicted watch count exceeds host budget"
                )
                return
            if not self._acquire_cross_process_watch_lock():
                return
            if not self._prepare_new_shared_watch(key):
                return
            observer = self._observer
            handler = self._handler
            if observer is None or handler is None:
                msg = "prepared workspace watch is missing its observer or handler"
                raise RuntimeError(msg)
            if self._observer is None:
                msg = "prepared workspace watch lost its lifecycle-owned observer"
                raise RuntimeError(msg)
            try:
                self._observer.schedule(handler, str(self._workspace), recursive=True)
                self._observer.start()
            except OSError as exc:
                self._abort_start_on_oserror(exc, observer)
                return
            except BaseException:
                self._abort_unexpected_watch_start(observer)
                raise
            self._register_new_shared_watch(key, observer, handler)
        logger.debug("Started workspace monitoring: {}", self._workspace)

    def _attach_existing_watch(self, key: str) -> bool:
        """Attach this monitor to a live in-process watch when one exists."""
        shared = self._shared_watches.get(key)
        if shared is not None and not shared.monitors:
            self._shared_watches.pop(key, None)
            self._stop_observer(shared.observer)
            shared = None
        if shared is None:
            return False
        shared.monitors.add(self)
        self._shared_key = key
        self._handler = shared.handler
        self._started = True
        self._awareness_status = _current_status()
        awareness_for_workspace(self._workspace).set_watch_active()
        return True

    def _watch_capacity_is_predicted(self) -> bool:
        """Return whether starting another recursive watch would exceed capacity."""
        return _watch_capacity_is_predicted(
            self._workspace,
            self._host_budget,
            self._directory_counter,
            self._live_watch_total,
        )

    def _activate_live_fallback(self, reason: str) -> None:
        """Record a visible live-read fallback for this workspace."""
        self._awareness_status = _live_fallback_status(reason)
        awareness_for_workspace(self._workspace).set_live_fallback(reason)

    def _prepare_new_shared_watch(self, key: str) -> bool:
        """Create the observer resources needed for a new shared watch."""
        observer = _create_watchdog_observer()
        if observer is None:
            self._release_cross_process_watch_lock()
            self._activate_live_fallback("observer_unavailable")
            return False
        self._observer = observer
        self._handler = _make_change_tracker(self._workspace, key)
        return True

    def _abort_unexpected_watch_start(self, observer: _ObserverProtocol) -> None:
        """Release partially started watch resources before propagating an error."""
        with suppress(BaseException):
            self._stop_observer(observer)
        self._observer = None
        self._handler = None
        self._release_cross_process_watch_lock()

    def _register_new_shared_watch(
        self, key: str, observer: _ObserverProtocol, handler: object
    ) -> None:
        """Publish a successfully started shared observer to all local leases."""
        shared = _SharedWorkspaceWatch(
            observer, handler, cross_process_owner_id=self._cross_process_owner_id
        )
        shared.monitors.add(self)
        self._shared_watches[key] = shared
        self._shared_key = key
        self._started = True
        self._awareness_status = _current_status()
        awareness_for_workspace(self._workspace).set_watch_active()

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
        awareness_for_workspace(self._workspace).record(src_path)
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
        """Stop monitoring the workspace and release its watch on every exit path.

        The observer reference is cleared even when its shutdown operation
        raises. This prevents a failed release from stranding lifecycle
        ownership and suppressing a later required registration; joining still
        runs after a failed ``stop`` before the original failure propagates.

        The cross-process watch lock is released only when the final
        in-process lease tears down the shared observer, so a non-final
        lease stopping does not let another process register a duplicate
        observer while this process's shared watch is still active
        (S-10 / DA-001 / DA-009).
        """
        if not self._started:
            # S-3: even a monitor that never acquired a watch (watchdog
            # unavailable, live_fallback) still represents a workflow
            # boundary, so release the process-level dirty-path debounce
            # timer before returning.
            from ralph.mcp.explore.dirty_paths import _dirty_scheduler

            _dirty_scheduler.on_workflow_complete()
            return
        key = self._shared_key
        self._started = False
        self._shared_key = None
        self._observer = None
        self._handler = None
        observer: _ObserverProtocol | None = None
        cross_process_owner_id: str | None = None
        if key is not None:
            with self._shared_lock:
                shared = self._shared_watches.get(key)
                if shared is not None:
                    shared.monitors.discard(self)
                    if not shared.monitors:
                        self._shared_watches.pop(key, None)
                        observer = shared.observer
                        cross_process_owner_id = shared.cross_process_owner_id
        if observer is not None:
            self._stop_observer(observer)
            release_workspace_awareness(self._workspace)
        # S-10: release the cross-process watch lock only when the final
        # in-process lease tore down the shared observer, using the owner
        # id stored on the shared watch (not the per-monitor id, which
        # could belong to a lease that stopped before the final one).
        if cross_process_owner_id is not None:
            CrossProcessWatchLock.release(self._workspace, cross_process_owner_id)
        # S-3: release the process-level dirty-path debounce timer on
        # every workspace lease release so a completed/cancelled/failed
        # workflow never strands a deferred reindex fire. The scheduler
        # is owned by ``ralph.mcp.explore.dirty_paths``; ``WorkspaceMonitor``
        # does NOT construct its own (single shared owner).
        from ralph.mcp.explore.dirty_paths import _dirty_scheduler

        _dirty_scheduler.on_workflow_complete()
        logger.debug(
            "Stopped workspace monitoring: {} ({} events)",
            self._workspace,
            self._event_count,
        )

    @classmethod
    def _stop_observer(cls, observer: _ObserverProtocol) -> None:
        try:
            observer.stop()
        finally:
            observer.join(5)

    def _abort_start_on_oserror(
        self, exc: OSError, observer: _ObserverProtocol
    ) -> None:
        """Clean up after an OSError during observer start.

        Tears down the partially-started observer, releases the
        cross-process lock, and sets a bounded ``live_fallback``
        awareness status. Returns normally (caller returns from
        ``start``) for recognized capacity/failure errnos; re-raises
        ``exc`` for unrecognized errnos so they propagate.
        """
        with suppress(BaseException):
            self._stop_observer(observer)
        self._observer = None
        self._handler = None
        self._release_cross_process_watch_lock()
        if exc.errno in (errno.EMFILE, errno.ENOSPC):
            self._awareness_status = _live_fallback_status("watch_capacity")
            awareness_for_workspace(self._workspace).set_live_fallback("watch_capacity")
            logger.warning("Workspace monitoring unavailable: inotify limit reached")
            return
        if exc.errno is None:
            self._awareness_status = _live_fallback_status("observer_start_failed")
            awareness_for_workspace(self._workspace).set_live_fallback(
                "observer_start_failed"
            )
            logger.opt(exception=True).warning(
                "Workspace watch registration failed; falling back to live reads"
            )
            return
        raise exc

    def _release_cross_process_watch_lock(self) -> None:
        """Release the cross-process watch lock when this lease owns it (S-10).

        A no-op when this monitor never won the cross-process lock (e.g. it
        joined an existing in-process shared watch, or it entered
        ``live_fallback`` because another process held the lock). The owner
        id is cleared before the release call so a reentrant stop is safe.
        """
        if self._cross_process_owner_id is None:
            return
        owner_id = self._cross_process_owner_id
        self._cross_process_owner_id = None
        CrossProcessWatchLock.release(self._workspace, owner_id)

    def _acquire_cross_process_watch_lock(self) -> bool:
        """Try to claim the cross-process watch lock for this lease (S-10).

        Returns ``True`` when this lease may proceed to schedule an observer
        (the lock was free and is now held by this process, or this process
        already holds it). Returns ``False`` when another process holds the
        lock; the awareness status is updated to ``live_fallback`` and the
        caller must return without scheduling.
        """
        holder = CrossProcessWatchLock.try_acquire(self._workspace)
        if holder is not None:
            self._awareness_status = _live_fallback_status("cross_process_holder")
            awareness_for_workspace(self._workspace).set_live_fallback(
                "cross_process_holder"
            )
            return False
        self._cross_process_owner_id = CrossProcessWatchLock.claimed_owner_id(
            self._workspace
        )
        return True

    @classmethod
    def _record_event_for_key(cls, key: str, src_path: str) -> None:
        """Deliver one observer event to every active lease for this workspace."""
        with cls._shared_lock:
            shared = cls._shared_watches.get(key)
            monitors = tuple(shared.monitors) if shared is not None else ()
        for monitor in monitors:
            monitor.record_event(src_path)

    def dispatch_event(self, event: object) -> None:
        """Dispatch a watchdog-style event through the per-monitor handler.

        Exposed for black-box tests that drive the handler directly
        with a synthetic event (FakeEvent). Production callers do not
        need this -- the watchdog backend dispatches events into the
        handler returned by ``_make_change_tracker(self)`` itself.

        The handler routes in-root source paths, or an in-root move
        destination when the source is outside, through ``record_event``;
        this mirrors the production watchdog dispatch path.

        Args:
            event: A watchdog-style event object. The handler
                duck-types via the ``_HasSrcPath`` protocol so any
                object with the expected attribute is accepted.
        """
        if self._handler is None:
            return
        handler = cast(
            "_HandlerWithDispatch", self._handler
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        handler.dispatch(event)

    @property
    def awareness_status(self) -> dict[str, object]:
        """Return the current observation mode and its freshness contract.

        A capacity failure does not pretend the workspace is current: callers
        receive ``live_fallback`` and can reconcile through their existing
        bounded live-read boundary before a knowledge-dependent operation.
        """
        return dict(self._awareness_status)

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
