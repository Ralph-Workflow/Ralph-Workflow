"""The workspace monitor may not stand between Ralph and an agent launch.

``invoke_agent`` starts a :class:`WorkspaceMonitor` AFTER it logs
``Invoking agent: <argv>`` and BEFORE it spawns the agent process. Every
step of that start is optional -- the monitor is an activity signal for
the idle watchdog, nothing the agent needs -- but on the way in it reads
the host's live inotify budget by sweeping ``/proc/<pid>/fdinfo`` and
counts the workspace's directories with ``os.walk``, neither of which
has a time bound. A hung network mount inside the workspace, or one
process in uninterruptible sleep, therefore parks the run between those
two lines: the operator sees the argv, no agent process is ever created,
no file is ever written, and nothing times out, because every watchdog
Ralph owns lives on the far side of a spawn that never happened.

The rule these tests pin is that the probe fails OPEN: when it cannot
answer quickly, the monitor gives up on watching and the launch
proceeds.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.invoke._watch_capacity import CAPACITY_PROBE_BUDGET_SECONDS
from ralph.agents.invoke._workspace import STOP_OBSERVER_BUDGET_SECONDS, WorkspaceMonitor
from ralph.workspace.awareness import awareness_for_workspace, release_workspace_awareness

if TYPE_CHECKING:
    import pytest

#: The probe budget these tests give the monitor. Production's default
#: is far larger; what is under test is that SOME bound exists and that
#: the monitor honours it, not the value.
_PROBE_BUDGET_SECONDS = 0.05

#: How long the hung probe answers in. Far beyond the suite's per-test
#: ceiling, which is what fails a regression here: an unbounded start
#: does not return slowly, it does not return, and the test times out
#: exactly as the run does.
_HUNG_PROBE_SECONDS = 20.0

#: How long the slow watch start walks for. Deliberately UNDER the
#: per-test ceiling: a step that blocks past it is rescued by the
#: harness's own alarm, and a rescued test proves nothing about a bound.
_SLOW_WATCH_SECONDS = 0.5


def _hung_counter(workspace: Path, cap: int) -> int | None:
    """A directory count that never answers, like a hung mount's walk."""
    del workspace, cap
    threading.Event().wait(timeout=_HUNG_PROBE_SECONDS)
    return 0


def test_a_capacity_probe_that_never_answers_does_not_park_the_start(
    tmp_path: Path,
) -> None:
    """A wedged capacity probe costs the WATCH, never the launch."""
    monitor = WorkspaceMonitor(
        tmp_path,
        host_budget=8192,
        directory_counter=_hung_counter,
        probe_budget_seconds=_PROBE_BUDGET_SECONDS,
    )
    try:
        monitor.start()
        # Reaching this line at all is half the assertion: an unbounded
        # probe never gets here, and the agent is never spawned.
        status = awareness_for_workspace(tmp_path).snapshot()
    finally:
        monitor.stop()
        release_workspace_awareness(tmp_path)

    assert status["mode"] == "live_fallback"


def test_a_probe_that_answers_is_still_believed(tmp_path: Path) -> None:
    """The time bound must not cost the monitor its real answer."""
    monitor = WorkspaceMonitor(
        tmp_path,
        host_budget=8192,
        directory_counter=lambda workspace, cap: 1,
        live_watch_total=0,
        probe_budget_seconds=_PROBE_BUDGET_SECONDS,
    )
    try:
        monitor.start()
        status = awareness_for_workspace(tmp_path).snapshot()
    finally:
        monitor.stop()
        release_workspace_awareness(tmp_path)

    assert status["mode"] != "live_fallback"


class _SlowObserver:
    """A watchdog observer whose recursive walk takes its time.

    On Linux, ``Observer.start()`` builds the whole inotify watch tree
        inline on the CALLER's thread -- watchdog runs
        ``InotifyEmitter.on_thread_start`` synchronously there, which
        constructs ``Inotify`` -> ``_add_dir_watch`` -> ``os.walk``. So the
        step the capacity estimate clears the way for does the very same
        recursive walk the estimate was bounded for. Scheduling is cheap on
        both backends; starting is where the tree is built.

        It blocks for less than the suite's per-test ceiling on purpose: a
        fake that blocks forever is rescued by that ceiling's own alarm,
        which makes an unbounded start look bounded. What proves the bound
        is that ``start()`` came back while this was still walking.
    """

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.finished = threading.Event()
        self.join_timeouts: list[float | None] = []

    def schedule(self, _event_handler: object, path: str, **_kwargs: object) -> None:
        del path

    def start(self) -> None:
        self.entered.set()
        threading.Event().wait(timeout=_SLOW_WATCH_SECONDS)
        self.finished.set()

    def stop(self) -> None:
        return

    def join(self, timeout: float | None = None) -> None:
        self.join_timeouts.append(timeout)

    def is_alive(self) -> bool:
        return False


def test_a_slow_watch_start_does_not_park_the_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bounding the ESTIMATE is not bounding the walk it estimates.

    The capacity probe answering "there is room" is the common case, and
    it hands control straight to a step that walks the same tree with no
    bound of its own -- holding the monitor's process-global lock, still
    before any agent process exists.
    """
    observer = _SlowObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer", lambda: observer
    )
    monitor = WorkspaceMonitor(
        tmp_path,
        host_budget=8192,
        directory_counter=lambda workspace, cap: 1,
        live_watch_total=0,
        probe_budget_seconds=_PROBE_BUDGET_SECONDS,
    )
    try:
        monitor.start()
        still_walking = not observer.finished.is_set()
        status = awareness_for_workspace(tmp_path).snapshot()
    finally:
        monitor.stop()
        release_workspace_awareness(tmp_path)

    assert observer.entered.is_set(), "the watch start was never reached"
    assert still_walking, "the launch waited for the whole recursive walk"
    assert status["mode"] == "live_fallback"


def test_the_shipped_probe_budget_is_finite_and_small() -> None:
    """The bound tests inject is not the bound production runs with.

    Both tests above pass their own budget, so nothing else pins the
    value a real launch is exposed to.
    """
    assert 0.0 < CAPACITY_PROBE_BUDGET_SECONDS <= 30.0
    assert 0.0 < STOP_OBSERVER_BUDGET_SECONDS <= 30.0


class _SlowWatchLock:
    """A cross-process watch lock whose sidecar I/O takes its time.

    ``try_acquire`` creates ``<workspace>/.agent/`` and opens the lock
    file there, and the ownership record is written to the same place.
    That is workspace filesystem I/O, in the same pre-spawn window and
    under the same process-global lock as the walk -- so the hung mount
    that parks one parks the other.
    """

    def __init__(self, *, stall: bool = True, holder: str | None = None) -> None:
        self.entered = threading.Event()
        self.finished = threading.Event()
        self.released = False
        self.stall = stall
        self.holder = holder

    def try_acquire(self, workspace: Path) -> str | None:
        del workspace
        self.entered.set()
        if self.stall:
            threading.Event().wait(timeout=_SLOW_WATCH_SECONDS)
        self.finished.set()
        return self.holder

    def claimed_owner_id(self, workspace: Path) -> str | None:
        del workspace
        return "owner-1"

    def last_released_holder(self, workspace: Path) -> str | None:
        del workspace
        return None

    def release(self, workspace: Path, owner_id: str) -> None:
        del workspace, owner_id
        self.released = True


def test_slow_watch_lock_io_does_not_park_the_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lock the watch is claimed under is workspace I/O too.

    Bounding the capacity probe and the observer start leaves the step
    BETWEEN them -- claiming the cross-process watch lock and writing
    the ownership sidecar, both under ``<workspace>/.agent/`` -- free to
    park the launch for as long as the filesystem takes.
    """
    lock = _SlowWatchLock()
    monkeypatch.setattr("ralph.agents.invoke._workspace.CrossProcessWatchLock", lock)
    monitor = WorkspaceMonitor(
        tmp_path,
        host_budget=8192,
        directory_counter=lambda workspace, cap: 1,
        live_watch_total=0,
        probe_budget_seconds=_PROBE_BUDGET_SECONDS,
    )
    try:
        monitor.start()
        still_waiting = not lock.finished.is_set()
        status = awareness_for_workspace(tmp_path).snapshot()
    finally:
        monitor.stop()
        release_workspace_awareness(tmp_path)

    assert lock.entered.is_set(), "the watch lock was never reached"
    assert still_waiting, "the launch waited for the whole lock acquisition"
    assert status["mode"] == "live_fallback"


def test_abandoning_a_slow_watch_start_releases_the_cross_process_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Giving up on the watch must give up the claim to it.

    The cross-process lock is what stops a second process registering an
    overlapping recursive observer on the same workspace. Holding it
    after abandoning the watch keeps that guarantee's cost -- no one
    else may watch -- with none of its benefit, until the process exits.
    """
    lock = _SlowWatchLock(stall=False)
    monkeypatch.setattr("ralph.agents.invoke._workspace.CrossProcessWatchLock", lock)
    observer = _SlowObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer", lambda: observer
    )
    monitor = WorkspaceMonitor(
        tmp_path,
        host_budget=8192,
        directory_counter=lambda workspace, cap: 1,
        live_watch_total=0,
        probe_budget_seconds=_PROBE_BUDGET_SECONDS,
    )
    try:
        monitor.start()
    finally:
        monitor.stop()
        release_workspace_awareness(tmp_path)

    assert observer.entered.is_set(), "the watch start was never reached"
    assert lock.released, "the abandoned watch kept its cross-process claim"


class _SlowStoppingObserver(_SlowObserver):
    """An observer whose teardown will not finish.

    ``observer.join(5)`` reads like the bound on a teardown, but the
    unbounded half is ``stop()``: watchdog joins every emitter thread
    there with no timeout. Teardown runs on the launch thread inside
    ``start()`` -- unwinding a failed watch, or discarding a stale one
    -- so an emitter that will not join is a launch that does not
    happen.
    """

    def __init__(self) -> None:
        super().__init__()
        self.stopping = threading.Event()
        self.stopped = threading.Event()

    def start(self) -> None:
        self.entered.set()

    def stop(self) -> None:
        self.stopping.set()
        threading.Event().wait(timeout=_SLOW_WATCH_SECONDS)
        self.stopped.set()


def test_a_teardown_that_will_not_finish_does_not_park_the_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unwinding a failed watch may not cost more than the watch would."""
    observer = _SlowStoppingObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer", lambda: observer
    )
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace.STOP_OBSERVER_BUDGET_SECONDS", _PROBE_BUDGET_SECONDS
    )
    monitor = WorkspaceMonitor(
        tmp_path,
        host_budget=8192,
        directory_counter=lambda workspace, cap: 1,
        live_watch_total=0,
        probe_budget_seconds=_PROBE_BUDGET_SECONDS,
    )
    try:
        monitor.start()
        monitor.stop()
        still_stopping = not observer.stopped.is_set()
    finally:
        release_workspace_awareness(tmp_path)

    assert observer.stopping.is_set(), "the teardown was never reached"
    assert still_stopping, "the caller waited for the whole teardown"


class _SlowSidecar:
    """A shared-awareness sidecar whose reads and writes take their time."""

    def __init__(self) -> None:
        self.polling = threading.Event()
        self.polled = threading.Event()
        self.writing = threading.Event()
        self.written = threading.Event()

    def poll(self) -> object:
        self.polling.set()
        threading.Event().wait(timeout=_SLOW_WATCH_SECONDS)
        self.polled.set()
        return None

    def begin_ownership(self, owner_id: str, *, prior_holder: str | None = None) -> None:
        del owner_id, prior_holder
        self.writing.set()
        threading.Event().wait(timeout=_SLOW_WATCH_SECONDS)
        self.written.set()

    def publish_changes(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def release(self, *args: object, **kwargs: object) -> None:
        del args, kwargs


def test_a_slow_ownership_write_does_not_park_the_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recording the claim is workspace I/O like taking it."""
    sidecar = _SlowSidecar()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace.shared_awareness_for_workspace", lambda _root: sidecar
    )
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace.CrossProcessWatchLock", _SlowWatchLock(stall=False)
    )
    monitor = WorkspaceMonitor(
        tmp_path,
        host_budget=8192,
        directory_counter=lambda workspace, cap: 1,
        live_watch_total=0,
        probe_budget_seconds=_PROBE_BUDGET_SECONDS,
    )
    try:
        monitor.start()
        still_writing = not sidecar.written.is_set()
        status = awareness_for_workspace(tmp_path).snapshot()
    finally:
        monitor.stop()
        release_workspace_awareness(tmp_path)

    assert sidecar.writing.is_set(), "the ownership write was never reached"
    assert still_writing, "the launch waited for the whole ownership write"
    assert status["mode"] == "live_fallback"


def test_a_slow_owner_sidecar_read_does_not_park_the_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The consumer path reads the workspace too.

    When another process already holds the watch lock, this one does not
    watch: it polls the owner's sidecar instead. That poll is a read
    under ``<workspace>/.agent/``, on the launch thread, in the same
    pre-spawn window as everything else here.
    """
    sidecar = _SlowSidecar()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace.shared_awareness_for_workspace", lambda _root: sidecar
    )
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace.CrossProcessWatchLock",
        _SlowWatchLock(stall=False, holder="another-process"),
    )
    monitor = WorkspaceMonitor(
        tmp_path,
        host_budget=8192,
        directory_counter=lambda workspace, cap: 1,
        live_watch_total=0,
        probe_budget_seconds=_PROBE_BUDGET_SECONDS,
    )
    try:
        monitor.start()
        still_polling = not sidecar.polled.is_set()
        status = awareness_for_workspace(tmp_path).snapshot()
    finally:
        monitor.stop()
        release_workspace_awareness(tmp_path)

    assert sidecar.polling.is_set(), "the owner sidecar was never polled"
    assert still_polling, "the launch waited for the whole sidecar read"
    assert status["mode"] == "live_fallback"


def test_the_watch_teardown_joins_with_a_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both halves of a teardown are bounded, not just the stop.

    ``observer.stop()`` runs under a budget; the ``join`` that follows it
    is the other half, and a join without a timeout waits on the same
    emitter thread the stop just failed to reach.
    """
    observer = _SlowStoppingObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer", lambda: observer
    )
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace.STOP_OBSERVER_BUDGET_SECONDS", _PROBE_BUDGET_SECONDS
    )
    monitor = WorkspaceMonitor(
        tmp_path,
        host_budget=8192,
        directory_counter=lambda workspace, cap: 1,
        live_watch_total=0,
        probe_budget_seconds=_PROBE_BUDGET_SECONDS,
    )
    try:
        monitor.start()
        monitor.stop()
    finally:
        release_workspace_awareness(tmp_path)

    assert observer.join_timeouts, "the teardown never joined the observer"
    assert all(timeout is not None for timeout in observer.join_timeouts), (
        "the teardown joined the observer with no timeout"
    )
