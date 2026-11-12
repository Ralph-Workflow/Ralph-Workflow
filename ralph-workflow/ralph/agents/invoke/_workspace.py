from __future__ import annotations

import errno
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from functools import partial
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, ClassVar, cast

from loguru import logger

from ralph.agents.idle_watchdog._workspace_change_kind import WorkspaceChangeKind
from ralph.agents.invoke._awareness_status import current_status, live_fallback_status
from ralph.agents.invoke._callback_arity import (
    TWO_ARG_ARITY,
    VALID_CALLBACK_ARITIES,
    callback_arity,
)
from ralph.agents.invoke._shared_awareness_publisher import ThrottledSidecarPublisher
from ralph.agents.invoke._workspace_change_coalescer import WorkspaceChangeCoalescer
from ralph.agents.invoke._workspace_seams import (
    WorkspaceWatchSeams,
    _CrossProcessWatchLockProtocol,
    _SharedWorkspaceWatch,
    bind_observer_overflow,
    make_change_tracker,
    shared_workspace_key,
)
from ralph.agents.invoke.watchdog_observer import (
    _WatchObserverProtocol,
    create_watchdog_observer,
)
from ralph.agents.watch_capacity import (
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
    is_sidecar_path,
    remove_shared_awareness_sidecar,
    shared_awareness_for_workspace,
)
from ralph.workspace.awareness import awareness_for_workspace, release_workspace_awareness

_create_watchdog_observer = create_watchdog_observer

CrossProcessWatchLockProtocol = _CrossProcessWatchLockProtocol

if TYPE_CHECKING:
    from ralph.agents.invoke._handler_with_dispatch import _HandlerWithDispatch
    from ralph.agents.invoke._workspace_change_classifier import WorkspaceChangeClassifier

STOP_OBSERVER_BUDGET_SECONDS = 5.0

_MAX_WORKSPACE_CHANGED_FILES = 512
WorkspaceEventCallback = Callable[[], None] | Callable[[WorkspaceChangeKind, float], None]


class WorkspaceMonitor:
    _shared_watches: ClassVar[dict[str, _SharedWorkspaceWatch]] = {}
    _shared_lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def shared_watch_snapshot(cls) -> list[tuple[str, tuple[WorkspaceMonitor, ...]]]:
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
        seams: WorkspaceWatchSeams | None = None,
    ) -> None:
        self._workspace = workspace_path
        self._observer: _WatchObserverProtocol | None = None
        self._shared_key: str | None = None
        self._started = False
        self._event_count = 0
        self._seen_files: dict[str, None] = {}
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
        self._sidecar_publisher: ThrottledSidecarPublisher | None = None
        self._awareness_status: dict[str, object] = live_fallback_status("unavailable")
        self._seams = seams if seams is not None else WorkspaceWatchSeams()

    def _watch_lock(self) -> _CrossProcessWatchLockProtocol:
        return self._seams.watch_lock or CrossProcessWatchLock

    def _create_observer(self) -> _WatchObserverProtocol | None:
        factory = self._seams.observer_factory or _create_watchdog_observer
        return factory()

    def start(self) -> None:
        if self._started:
            return

        key = shared_workspace_key(self._workspace)
        with self._shared_lock:
            if self._attach_existing_watch(key):
                return
            if self._watch_capacity_is_predicted():
                self._activate_live_fallback("watch_capacity_predicted")
                logger.warning(
                    "Workspace monitoring unavailable: predicted watch count exceeds host budget"
                )
                try:
                    from ralph.mcp.server._mcp_health_event import record_host_contention_signal

                    record_host_contention_signal("watch_capacity_predicted")
                except Exception:
                    pass
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
        return watch_capacity_is_predicted(
            self._workspace,
            self._host_budget,
            self._directory_counter,
            self._live_watch_total,
            self._probe_budget_seconds,
        )

    def _activate_live_fallback(self, reason: str) -> None:
        self._awareness_status = live_fallback_status(reason)
        awareness_for_workspace(self._workspace).set_live_fallback(reason)

    def _prepare_new_shared_watch(self, key: str) -> bool:
        observer = self._create_observer()
        if observer is None:
            self._release_cross_process_watch_lock()
            self._activate_live_fallback("observer_unavailable")
            return False
        self._observer = observer
        self._handler = make_change_tracker(
            self._workspace, partial(WorkspaceMonitor._record_event_for_key, key)
        )
        bind_observer_overflow(observer, self._workspace)
        return True

    def _start_observer_within_budget(self, observer: _WatchObserverProtocol) -> bool:
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
        self._observer = None
        self._handler = None
        self._release_cross_process_watch_lock()
        self._activate_live_fallback("watch_start_timed_out")

    def _abort_unexpected_watch_start(self, observer: _WatchObserverProtocol) -> None:
        with suppress(BaseException):
            self._stop_observer(observer)
        self._observer = None
        self._handler = None
        self._release_cross_process_watch_lock()

    def _register_new_shared_watch(
        self, key: str, observer: _WatchObserverProtocol, handler: object
    ) -> None:
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
        if is_sidecar_path(self._workspace, src_path):
            return
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
        root = PurePosixPath(Path(self._workspace).resolve(strict=False).as_posix())
        resolved = Path(src_path).resolve(strict=False).as_posix()
        return self._classifier.classify(resolved, workspace_root=root)

    def _publish_shared_awareness_change(self, src_path: str) -> None:
        if self._shared_awareness_io_error is not None:
            self._activate_live_fallback("shared_awareness_io_failed")
            return
        try:
            relative = Path(src_path).absolute().relative_to(self._workspace.absolute())
        except ValueError:
            return
        if self._sidecar_publisher is None:
            self._sidecar_publisher = ThrottledSidecarPublisher(
                sidecar=partial(
                    self._seams.sidecar_factory or shared_awareness_for_workspace,
                    self._workspace,
                ),
                now=self._now,
                on_failure=self._activate_live_fallback,
            )
        self._sidecar_publisher.add(relative.as_posix())

    def stop(self) -> None:
        if not self._started:
            from ralph.mcp.explore.dirty_paths import _dirty_scheduler

            _dirty_scheduler.on_workflow_complete()
            return
        key = self._shared_key
        self._started = False
        self._shared_key = None
        if self._sidecar_publisher is not None:
            self._sidecar_publisher.close()
            self._sidecar_publisher = None
        self._observer = None
        self._handler = None
        observer: _WatchObserverProtocol | None = None
        handler_to_close: WorkspaceChangeCoalescer | None = None
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
                        if isinstance(shared.handler, WorkspaceChangeCoalescer):
                            handler_to_close = shared.handler
        if observer is not None:
            self._stop_observer(observer)
            if handler_to_close is not None:
                handler_to_close.close()
            release_workspace_awareness(self._workspace)
            (self._seams.sidecar_remover or remove_shared_awareness_sidecar)(self._workspace)
        if cross_process_owner_id is not None:
            self._watch_lock().release(self._workspace, cross_process_owner_id)
        if self._shared_consumer or self._owns_shared_awareness:
            from ralph.workspace._shared_awareness import release_shared_awareness

            release_shared_awareness(self._workspace)
        self._owns_shared_awareness = False
        self._shared_consumer = False
        from ralph.mcp.explore.dirty_paths import _dirty_scheduler

        _dirty_scheduler.on_workflow_complete()
        logger.debug(
            "Stopped workspace monitoring: {} ({} events)",
            self._workspace,
            self._event_count,
        )

    def _stop_observer(self, observer: _WatchObserverProtocol) -> None:
        failed: list[tuple[type[BaseException], str]] = []
        budget = self._seams.stop_observer_budget_seconds
        if budget is None:
            budget = STOP_OBSERVER_BUDGET_SECONDS

        def _stop() -> bool:
            try:
                observer.stop()
            except BaseException as exc:
                failed.append((type(exc), str(exc)))
                return False
            return True

        try:
            call_within_budget(_stop, fallback=None, budget_seconds=budget)
        finally:
            observer.join(5)
        if failed:
            kind, message = failed[0]
            raise kind(message)

    def _abort_start_on_oserror(self, exc: OSError, observer: _WatchObserverProtocol) -> None:
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
        if self._cross_process_owner_id is None:
            return
        owner_id = self._cross_process_owner_id
        self._cross_process_owner_id = None
        workspace = self._workspace
        lock = self._watch_lock()
        call_within_budget(
            lambda: lock.release(workspace, owner_id),
            fallback=None,
            budget_seconds=self._probe_budget_seconds,
        )

    def _acquire_cross_process_watch_lock(self) -> bool:
        workspace = self._workspace
        acquired: list[str | None] = []
        lock_io_failed: list[bool] = []
        lock = self._watch_lock()

        def _try_acquire() -> bool:
            try:
                acquired.append(lock.try_acquire(workspace))
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
        self._cross_process_owner_id = lock.claimed_owner_id(self._workspace)
        if self._cross_process_owner_id is None:
            self._activate_live_fallback("cross_process_holder")
            return False
        self._prior_lock_holder = lock.last_released_holder(self._workspace)
        owner_id = self._cross_process_owner_id
        prior_holder = self._prior_lock_holder
        ownership_error: list[SharedAwarenessError] = []
        sidecar_factory = self._seams.sidecar_factory or shared_awareness_for_workspace

        def _begin_ownership() -> bool:
            try:
                sidecar_factory(workspace).begin_ownership(owner_id, prior_holder=prior_holder)
            except SharedAwarenessError as exc:
                ownership_error.append(exc)
            return True

        answered_ownership = call_within_budget(
            _begin_ownership, fallback=None, budget_seconds=self._probe_budget_seconds
        )
        if ownership_error:
            self._shared_awareness_io_error = ownership_error[0]
        if answered_ownership is None:
            self._activate_live_fallback("watch_lock_io_timed_out")
            return False
        self._owns_shared_awareness = True
        return True

    def _enter_shared_awareness_consumer(self, holder: str) -> None:
        sidecar = (self._seams.sidecar_factory or shared_awareness_for_workspace)(self._workspace)
        polled: list[SharedAwarenessState] = []
        poll_failed: list[bool] = []

        def _poll() -> bool:
            try:
                polled.append(sidecar.poll())
            except SharedAwarenessError:
                poll_failed.append(True)
                return False
            return True

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
        with cls._shared_lock:
            shared = cls._shared_watches.get(key)
            monitors = tuple(shared.monitors) if shared is not None else ()
        for monitor in monitors:
            monitor.record_event(src_path)

    def dispatch_event(self, event: object) -> None:
        if self._handler is None:
            return
        handler = cast("_HandlerWithDispatch", self._handler)
        handler.dispatch(event)
        if isinstance(handler, WorkspaceChangeCoalescer):
            handler.flush()

    @property
    def awareness_status(self) -> dict[str, object]:
        return dict(self._awareness_status)

    @property
    def event_count(self) -> int:
        return self._event_count

    @property
    def last_event_at(self) -> float | None:
        return self._last_event_at

    def reset_last_event_at(self) -> None:
        self._last_event_at = None
        self._event_count = 0
        self._seen_files.clear()

    def set_on_event(self, on_event: WorkspaceEventCallback | None) -> None:
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
        return set(self._seen_files)
