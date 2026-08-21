"""Workspace monitoring for file changes during agent execution."""

from __future__ import annotations

import errno
import importlib
import threading
import time
import weakref
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Protocol, cast

from loguru import logger

from ralph.agents.idle_watchdog._workspace_change_kind import WorkspaceChangeKind
from ralph.agents.invoke._awareness_status import current_status, live_fallback_status
from ralph.agents.invoke._callback_arity import (
    TWO_ARG_ARITY,
    VALID_CALLBACK_ARITIES,
    callback_arity,
)
from ralph.agents.invoke._has_dest_path import _HasDestPath
from ralph.agents.invoke._has_src_path import _HasSrcPath
from ralph.agents.invoke._watch_capacity import (
    CAPACITY_PROBE_BUDGET_SECONDS,
    DirectoryCounter,
    call_within_budget,
    watch_capacity_is_predicted,
)
from ralph.workspace._cross_process_watch_lock import (
    CrossProcessWatchLock,
    WatchLockIOError,
)
from ralph.workspace._shared_awareness import (
    SharedAwarenessError,
    SharedAwarenessState,
    remove_shared_awareness_sidecar,
    shared_awareness_for_workspace,
)
from ralph.workspace.awareness import awareness_for_workspace, release_workspace_awareness

if TYPE_CHECKING:
    from ralph.agents.invoke._handler_with_dispatch import _HandlerWithDispatch
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


#: How long tearing a watch down may take before the caller stops
#: waiting for it. Teardown runs on the launch thread inside ``start()``,
#: so an emitter that will not join is a launch that does not happen.
STOP_OBSERVER_BUDGET_SECONDS = 5.0

_MAX_WORKSPACE_CHANGED_FILES = 512


#: Union of the two valid on_event callback signatures. A callback
#: with no required positional args (the legacy 0-arg binding) is
#: accepted for backward compatibility; a callback with exactly 2
#: required positional args (the production 2-arg binding) carries
#: ``(kind, weight)`` so the watchdog's per-kind counter receives
#: real classifications. The ``__post_init__`` arity check rejects
#: any other arity at construction time.
WorkspaceEventCallback = Callable[[], None] | Callable[[WorkspaceChangeKind, float], None]


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
        directory_counter: DirectoryCounter | None = None,
        live_watch_total: int | None = None,
        probe_budget_seconds: float | None = None,
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
                binary weight. When omitted (or ``None``), a default
                classifier is used that keeps ``SOURCE`` paths at weight
                ``1.0`` and drops Ralph-managed internal paths (``.agent``
                bookkeeping, caches, logs) at weight ``0.0`` so internal
                activity never recursively triggers workspace awareness
                (S-2 internal-path exclusion). An explicit classifier
                overrides this; events with weight ``0.0`` are dropped
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
            arity = callback_arity(on_event)
            if arity not in VALID_CALLBACK_ARITIES:
                msg = (
                    f"WorkspaceMonitor on_event callback has arity {arity};"
                    f" expected 0 (legacy) or 2 (production-style forwarding of"
                    f" (kind, weight))."
                )
                raise ValueError(msg)
        self._on_event: WorkspaceEventCallback | None = on_event
        if classifier is None:
            # S-2: default to the source-only classifier so Ralph-managed
            # internal paths (``.agent`` bookkeeping, caches, logs) are
            # dropped on the production observer/sidecar route, which
            # bypasses any explicit per-lease classifier.
            from ralph.agents.invoke._workspace_change_classifier import (
                WorkspaceChangeClassifier,
                _normalize_workspace_change_weights,
            )

            classifier = WorkspaceChangeClassifier(
                weights=_normalize_workspace_change_weights({"source": 1.0})
            )
        self._classifier: WorkspaceChangeClassifier = classifier
        self._host_budget = host_budget
        self._directory_counter = directory_counter
        self._live_watch_total = live_watch_total
        self._probe_budget_seconds = (
            probe_budget_seconds
            if probe_budget_seconds is not None
            else CAPACITY_PROBE_BUDGET_SECONDS
        )
        self._handler: object | None = None
        self._cross_process_owner_id: str | None = None
        self._owns_shared_awareness = False
        self._shared_consumer = False
        self._prior_lock_holder: str | None = None
        self._shared_awareness_io_error: SharedAwarenessError | None = None
        self._awareness_status: dict[str, object] = live_fallback_status("unavailable")

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
            # The canonical recursive watch: ONE schedule call, on the
            # lifecycle-owned observer, statically here, because
            # ``audit_fsevents_watch_consolidation`` requires exactly
            # that shape. It is the one step in this method left
            # unbounded: the fsevents emitter resolves the workspace
            # path here (``realpath``), so a mount hung on the ROOT
            # itself can still park it. Every other step this method
            # reaches -- the tree walk in the observer START below, the
            # lock's claim AND release, the sidecar's write, read and
            # epoch claim, the teardown -- runs under a budget.
            scheduled = True
            try:
                self._observer.schedule(handler, str(self._workspace), recursive=True)
            except OSError as exc:
                self._abort_start_on_oserror(exc, observer)
                scheduled = False
            except BaseException:
                self._abort_unexpected_watch_start(observer)
                raise
            if not scheduled or not self._start_observer_within_budget(observer):
                return
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
        self._awareness_status = current_status()
        awareness_for_workspace(self._workspace).set_watch_active()
        return True

    def _watch_capacity_is_predicted(self) -> bool:
        """Return whether starting another recursive watch would exceed capacity."""
        return watch_capacity_is_predicted(
            self._workspace,
            self._host_budget,
            self._directory_counter,
            self._live_watch_total,
            self._probe_budget_seconds,
        )

    def _activate_live_fallback(self, reason: str) -> None:
        """Record a visible live-read fallback for this workspace."""
        self._awareness_status = live_fallback_status(reason)
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

    def _start_observer_within_budget(self, observer: _ObserverProtocol) -> bool:
        """Start the scheduled watch, giving up if it takes too long.

        Bounding the capacity ESTIMATE is not bounding the walk it
        estimates. When the estimate says there is room -- the common
        case -- this is what runs next, and on Linux it is where the
        whole recursive inotify tree is built, inline on this thread
        (watchdog's ``BaseObserver.start`` -> ``InotifyEmitter.
        on_thread_start`` -> ``Inotify`` -> ``_add_dir_watch`` ->
        ``os.walk``). One hung mount inside the workspace parks it
        there, still before the agent process exists and still holding
        the class-wide lock.

        Nothing about the monitor is captured by the worker: a strong
        reference to ``self`` from a thread that outlives this call
        would keep a lease alive in the class-wide watch table that
        every later monitor for this workspace would attach to.

        Args:
            observer: The scheduled, not-yet-started observer.

        Returns:
            Whether the watch started and may be registered.
        """
        failure: list[OSError] = []
        unexpected: list[BaseException] = []

        def _start_observer() -> bool:
            try:
                observer.start()
            except OSError as exc:
                failure.append(exc)
                return False
            except BaseException as exc:
                unexpected.append(exc)
                return False
            return True

        started: bool | None = call_within_budget(
            _start_observer, fallback=None, budget_seconds=self._probe_budget_seconds
        )
        if started is None:
            self._abandon_slow_watch_start()
            return False
        if unexpected:
            self._abort_unexpected_watch_start(observer)
            raise unexpected[0]
        if failure:
            self._abort_start_on_oserror(failure[0], observer)
            return False
        return started

    def _abandon_slow_watch_start(self) -> None:
        """Give up on a watch that is taking longer than the launch can wait.

        The observer is deliberately NOT stopped: stopping it means
        waiting on the very thread that has not come back. It is left to
        finish its walk against a watch this process has forgotten,
        which costs one thread and one live watch tree against parking
        the run.

        Forgotten is not silent. The orphan's handler still dispatches
        by scope key, so a LATER monitor registering that key can be
        handed the orphan's events, and the watches it holds still count
        against the host's inotify budget for every later capacity
        probe. Both inflate an activity signal rather than corrupt
        anything, which is why they are accepted here; a run that never
        returns is not.
        """
        self._observer = None
        self._handler = None
        self._release_cross_process_watch_lock()
        self._activate_live_fallback("watch_start_timed_out")

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
        self._awareness_status = current_status()
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
        if self._owns_shared_awareness:
            self._publish_shared_awareness_change(src_path)
        self._seen_files.pop(src_path, None)
        self._seen_files[src_path] = None
        while len(self._seen_files) > _MAX_WORKSPACE_CHANGED_FILES:
            oldest = next(iter(self._seen_files))
            del self._seen_files[oldest]
        self._event_count += 1
        self._last_event_at = self._now()
        if self._on_event is not None:
            try:
                arity = callback_arity(self._on_event)
                if arity == TWO_ARG_ARITY:
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

        The monitor's classifier is always set (the constructor installs a
        default source-only classifier when none is supplied), so this is a
        direct delegation. This helper is the canonical seam for tests and
        dry-run checks that want to inspect the classifier output without
        recording an event.
        """
        return self._classifier.classify(src_path)

    def _publish_shared_awareness_change(self, src_path: str) -> None:
        """Publish one observed source change to the cross-process sidecar.

        Owner-only: a sidecar I/O failure is surfaced as explicit
        ``live_fallback`` (bounded live reconciliation) rather than a
        duplicate observer or silently dropped change (S-2).
        """
        if self._shared_awareness_io_error is not None:
            self._activate_live_fallback("shared_awareness_io_failed")
            return
        sidecar = shared_awareness_for_workspace(self._workspace)
        try:
            relative = Path(src_path).absolute().relative_to(self._workspace.absolute())
        except ValueError:
            return
        try:
            sidecar.publish_changes([relative.as_posix()])
        except SharedAwarenessError as exc:
            with suppress(SharedAwarenessError):
                sidecar.publish_error(str(exc))
            self._activate_live_fallback("shared_awareness_io_failed")

    def stop(self) -> None:
        """Stop monitoring the workspace and release its watch on every exit path.

        The observer reference is cleared even when its shutdown operation
        raises. This prevents a failed release from stranding lifecycle
        ownership and suppressing a later required registration; joining still
        runs after a failed ``stop`` before the original failure propagates.

        The cross-process watch lock and shared-awareness sidecar ownership
        are released only when the final in-process lease tears down the
        shared observer, so a non-final lease stopping does not let another
        process register a duplicate observer while this process's shared
        watch is still active (S-10 / DA-001 / DA-009). A consumer lease
        (no observer of its own) detaches without touching either (S-2).
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
            remove_shared_awareness_sidecar(self._workspace)
        # S-10: release the cross-process watch lock only when the final
        # in-process lease tore down the shared observer, using the owner
        # id stored on the shared watch (not the per-monitor id, which
        # could belong to a lease that stopped before the final one).
        if cross_process_owner_id is not None:
            CrossProcessWatchLock.release(self._workspace, cross_process_owner_id)
        # S-2: a shared-awareness consumer lease detaches from the owner
        # sidecar; the file itself remains for the owner and future owners.
        if self._shared_consumer or self._owns_shared_awareness:
            from ralph.workspace._shared_awareness import release_shared_awareness

            release_shared_awareness(self._workspace)
        self._owns_shared_awareness = False
        self._shared_consumer = False
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
        """Tear a watch down without letting the teardown park the caller.

        ``observer.join(5)`` reads like the bound here, but the
        unbounded half is ``stop()``: watchdog's ``BaseObserver.stop``
        runs ``on_thread_stop`` -> ``unschedule_all`` ->
        ``_clear_emitters``, which joins every emitter thread with NO
        timeout (and on Linux ``InotifyBuffer.close`` joins again). This
        is reached from ``start()`` -- tearing down a stale shared watch,
        or unwinding a failed one -- on the launch thread, holding the
        class-wide lock, in the window before the agent process exists.
        A wedged emitter parks every workspace's monitor, not just this
        one's.
        """
        failed: list[tuple[type[BaseException], str]] = []

        def _stop() -> bool:
            try:
                observer.stop()
            except BaseException as exc:
                # Only the type and the message cross back: see below.
                failed.append((type(exc), str(exc)))
                return False
            return True

        try:
            call_within_budget(_stop, fallback=None, budget_seconds=STOP_OBSERVER_BUDGET_SECONDS)
        finally:
            observer.join(5)
        if failed:
            # A FRESH exception, never the captured one. Holding the
            # original keeps its traceback, the traceback keeps the
            # frames that were live when the teardown blew up, and one
            # of those frames holds the monitor. Leases are held weakly,
            # so a monitor that outlives refcounting until the next
            # cyclic collection leaves a live-looking lease behind and
            # the next monitor for that workspace attaches to a watch
            # that should have been gone. The type and the message are
            # what callers act on.
            kind, message = failed[0]
            raise kind(message)

    def _abort_start_on_oserror(self, exc: OSError, observer: _ObserverProtocol) -> None:
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
            self._awareness_status = live_fallback_status("watch_capacity")
            awareness_for_workspace(self._workspace).set_live_fallback("watch_capacity")
            logger.warning("Workspace monitoring unavailable: inotify limit reached")
            return
        if exc.errno is None:
            self._awareness_status = live_fallback_status("observer_start_failed")
            awareness_for_workspace(self._workspace).set_live_fallback("observer_start_failed")
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
        workspace = self._workspace
        # Bounded like the claim it undoes. Releasing unlocks and closes
        # a file under ``<workspace>/.agent/``, and this runs on the
        # GIVE-UP path -- so a mount that hung after the claim would
        # park the launch inside the very release it performed because
        # it was giving up.
        call_within_budget(
            lambda: CrossProcessWatchLock.release(workspace, owner_id),
            fallback=None,
            budget_seconds=self._probe_budget_seconds,
        )

    def _acquire_cross_process_watch_lock(self) -> bool:
        """Try to claim the cross-process watch lock for this lease (S-2/S-10).

        Returns ``True`` when this lease may proceed to schedule an observer
        (the lock was free and is now held by this process, or this process
        already holds it). Returns ``False`` when another process holds the
        lock; the lease polls the owner's shared-awareness sidecar instead of
        registering an overlapping observer (S-2). Sidecar or lock I/O
        failure yields explicit ``live_fallback`` rather than a standalone
        duplicate observer.
        """
        # Every call in this method is workspace filesystem I/O --
        # ``<workspace>/.agent/`` is created, the lock file opened, the
        # ownership sidecar written -- in the same pre-spawn window, and
        # under the same class-wide lock, as the walk the capacity probe
        # and the observer start are bounded for. A hung mount parks it
        # here just as readily, so it is bounded on the same terms and
        # fails the same way: no watch, launch proceeds.
        workspace = self._workspace
        acquired: list[str | None] = []
        lock_io_failed: list[bool] = []

        def _try_acquire() -> bool:
            try:
                acquired.append(CrossProcessWatchLock.try_acquire(workspace))
            except WatchLockIOError:
                lock_io_failed.append(True)
                return False
            return True

        answered = call_within_budget(
            _try_acquire, fallback=None, budget_seconds=self._probe_budget_seconds
        )
        if lock_io_failed:
            self._activate_live_fallback("shared_awareness_io_failed")
            return False
        if answered is None or not acquired:
            self._activate_live_fallback("watch_lock_io_timed_out")
            return False
        holder = acquired[0]
        if holder is not None:
            self._enter_shared_awareness_consumer(holder)
            return False
        self._cross_process_owner_id = CrossProcessWatchLock.claimed_owner_id(self._workspace)
        if self._cross_process_owner_id is None:
            self._activate_live_fallback("cross_process_holder")
            return False
        self._prior_lock_holder = CrossProcessWatchLock.last_released_holder(self._workspace)
        owner_id = self._cross_process_owner_id
        prior_holder = self._prior_lock_holder
        ownership_error: list[SharedAwarenessError] = []

        def _begin_ownership() -> bool:
            try:
                shared_awareness_for_workspace(workspace).begin_ownership(
                    owner_id, prior_holder=prior_holder
                )
            except SharedAwarenessError as exc:
                ownership_error.append(exc)
            return True

        answered_ownership = call_within_budget(
            _begin_ownership, fallback=None, budget_seconds=self._probe_budget_seconds
        )
        if ownership_error:
            self._shared_awareness_io_error = ownership_error[0]
        if answered_ownership is None:
            # The sidecar write never answered. Recording ownership of a
            # claim that was never published is the same error the two
            # calls above refuse to make.
            self._activate_live_fallback("watch_lock_io_timed_out")
            return False
        self._owns_shared_awareness = True
        return True

    def _enter_shared_awareness_consumer(self, holder: str) -> None:
        """Poll the owning process's sidecar instead of registering an observer.

        Owner-published change paths are durably claimed into the
        process-local awareness before the lease reports a non-current
        freshness, so a crash cannot silently drop owner-published changes.
        A sidecar that is unreadable, corrupt, or carries an owner-side
        error yields explicit ``live_fallback`` (S-2).
        """
        sidecar = shared_awareness_for_workspace(self._workspace)
        polled: list[SharedAwarenessState] = []
        poll_failed: list[bool] = []

        def _poll() -> bool:
            try:
                polled.append(sidecar.poll())
            except SharedAwarenessError:
                poll_failed.append(True)
                return False
            return True

        # Reading the owner's sidecar is workspace I/O on the launch
        # thread, exactly like claiming the lock above.
        answered = call_within_budget(
            _poll, fallback=None, budget_seconds=self._probe_budget_seconds
        )
        if poll_failed:
            self._activate_live_fallback("shared_awareness_io_failed")
            return
        if answered is None or not polled:
            self._activate_live_fallback("watch_lock_io_timed_out")
            return
        state = polled[0]
        self._shared_consumer = True
        self._started = True
        awareness = awareness_for_workspace(self._workspace)
        paths = list(state.paths)
        if paths or state.overflowed:
            for path in paths:
                awareness.record_relative(path)
            try:
                from ralph.mcp.explore.dirty_paths import (
                    enqueue_workspace_dirty_paths,
                )

                enqueue_workspace_dirty_paths(self._workspace, paths)
            except (ImportError, OSError):
                pass
        # The sidecar's own lock is held across its file I/O, so this
        # is an unbounded acquisition on the launch thread whenever
        # another lease's abandoned worker still holds it.
        epoch = state.epoch
        call_within_budget(
            lambda: sidecar.claim_epoch(epoch),
            fallback=None,
            budget_seconds=self._probe_budget_seconds,
        )
        self._awareness_status = {
            "mode": "shared_awareness",
            "freshness": awareness.snapshot()["freshness"],
            "cause": "cross_process_holder",
            "shared_owner": holder,
            "shared_epoch": state.epoch,
            "automatic_recovery": True,
            "safe_next_action": (
                "Consuming the owning process's shared awareness; no duplicate "
                "observer is registered."
            ),
        }

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
            arity = callback_arity(on_event)
            if arity not in VALID_CALLBACK_ARITIES:
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
