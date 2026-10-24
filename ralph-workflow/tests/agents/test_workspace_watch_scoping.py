"""Black-box tests for the workspace file-watcher (single recursive root watch).

These tests prove ``WorkspaceMonitor.start()`` schedules exactly ONE
recursive watchdog watch on the workspace root for any classifier
configuration. Activity-counting behavior is preserved by the
``record_event`` classify-drop backstop (independent of watch
scheduling), so observable behavior is unchanged while the
fseventsd footprint shrinks from the previous 4+ overlapping
OS-recursive streams down to one.

No real filesystem or watchdog observer is touched. The three watch
collaborators ``WorkspaceMonitor`` reaches for -- the cross-process
watch lock, the observer factory and the shared-awareness sidecar --
are injected through the monitor's public constructor, so the tests
drive the collaboration contract instead of patching module internals.
The ``_FakeObserver`` records every ``schedule()`` call into a list
and no-ops ``start``/``stop``/``join``. All events flow through the
public ``monitor.dispatch_event`` seam (never the private handler)
so the rewritten tests prove the production dispatch wiring without
coupling to internals.
"""

from __future__ import annotations

import errno
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke._workspace import WorkspaceMonitor
from ralph.agents.invoke._workspace_change_classifier import (
    WorkspaceChangeClassifier,
    WorkspaceChangeKind,
)
from ralph.agents.invoke._workspace_seams import WorkspaceWatchSeams
from ralph.workspace._shared_awareness import (
    SharedAwarenessSidecar,
    SharedAwarenessState,
    release_shared_awareness,
)
from ralph.workspace.awareness import release_workspace_awareness

if TYPE_CHECKING:
    from ralph.agents.invoke._watch_capacity import DirectoryCounter
    from ralph.agents.invoke.watchdog_observer import WatchObserverFactory


class _FakeCrossProcessWatchLock:
    """In-memory stand-in for ``CrossProcessWatchLock`` that always succeeds."""

    def __init__(self) -> None:
        self._owner_id: str | None = None
        self._counter = 0

    def try_acquire(self, workspace_root: Path) -> str | None:
        del workspace_root
        self._counter += 1
        self._owner_id = f"us:{self._counter}"
        return None

    def claimed_owner_id(self, workspace_root: Path) -> str | None:
        del workspace_root
        return self._owner_id

    def last_released_holder(self, workspace_root: Path) -> str | None:
        del workspace_root
        return None

    def release(self, workspace_root: Path, owner_id: str) -> None:
        del workspace_root, owner_id
        self._owner_id = None


class _FakeSharedAwarenessSidecar(SharedAwarenessSidecar):
    """In-memory stand-in for the shared-awareness sidecar.

    Every member the workspace monitor drives is overridden, so the
    coordination contract stays observable without a sidecar file ever
    being written.
    """

    def __init__(self, workspace_root: Path) -> None:
        super().__init__(workspace_root)
        self.owner_id: str | None = None
        self._current_epoch = 0
        self.paths: list[str] = []
        self.overflowed = False

    @property
    def epoch(self) -> int:
        """Return the epoch the fake has published up to."""
        return self._current_epoch

    def begin_ownership(self, owner_id: str, *, prior_holder: str | None) -> int:
        del prior_holder
        self.owner_id = owner_id
        self._current_epoch += 1
        return self._current_epoch

    def publish_changes(self, paths: list[str], *, overflowed: bool = False) -> int:
        for path in paths:
            if path not in self.paths:
                self.paths.append(path)
        self.overflowed = self.overflowed or overflowed
        self._current_epoch += 1
        return self._current_epoch

    def publish_error(self, cause: str) -> None:
        del cause

    def end_ownership(self) -> None:
        self.owner_id = None

    def poll(self) -> SharedAwarenessState:
        return SharedAwarenessState(
            epoch=self._current_epoch,
            paths=list(self.paths),
            overflowed=self.overflowed,
            owner_id=self.owner_id or "unknown",
            changed=True,
        )

    def claim_epoch(self, epoch: int) -> None:
        del epoch


def _fixed_directory_counter(count: int | None) -> DirectoryCounter:
    """Return a counter that reports ``count`` directories without walking a tree."""

    def _counter(workspace: Path, *, cap: int) -> int | None:
        del workspace, cap
        return count

    return _counter


@pytest.fixture(autouse=True)
def _cleanup_shared_workspace_state() -> Iterator[None]:
    """Clear shared watch and awareness state between tests."""
    yield
    release_workspace_awareness(Path("/ws"))
    release_shared_awareness(Path("/ws"))


def _monitor(
    workspace: Path,
    observer_factory: WatchObserverFactory,
    *,
    lock: _FakeCrossProcessWatchLock | None = None,
    sidecar: SharedAwarenessSidecar | None = None,
    classifier: WorkspaceChangeClassifier | None = None,
    host_budget: int | None = 8192,
    directory_counter: DirectoryCounter | None = None,
    live_watch_total: int | None = 0,
) -> WorkspaceMonitor:
    """Build a monitor whose watch collaborators are in-memory doubles.

    The watch-scoping tests use synthetic workspace roots (``/ws``) that
    cannot host a real lock sidecar; injecting the collaborators keeps
    the coordination contract observable without filesystem access.
    """
    resolved_sidecar = sidecar if sidecar is not None else _FakeSharedAwarenessSidecar(workspace)
    return WorkspaceMonitor(
        workspace,
        classifier=classifier,
        host_budget=host_budget,
        directory_counter=(
            directory_counter if directory_counter is not None else _fixed_directory_counter(0)
        ),
        live_watch_total=live_watch_total,
        seams=WorkspaceWatchSeams(
            watch_lock=lock if lock is not None else _FakeCrossProcessWatchLock(),
            observer_factory=observer_factory,
            sidecar_factory=lambda _root: resolved_sidecar,
            sidecar_remover=lambda _root: None,
        ),
    )


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeEvent:
    """Stand-in for watchdog events carrying the fields we duck-type on."""

    def __init__(
        self,
        src_path: str,
        dest_path: str | None = None,
        is_directory: bool = False,
        event_type: str = "modified",
    ) -> None:
        self.src_path = src_path
        if dest_path is not None:
            self.dest_path = dest_path
        self.is_directory = is_directory
        self.event_type = event_type


class _FakeObserver:
    """Stand-in for ``watchdog.observers.Observer`` recording schedule calls."""

    def __init__(self) -> None:
        self.scheduled: list[tuple[object, str, bool]] = []
        self.started: bool = False
        self.stopped: bool = False
        self.joined: bool = False

    def schedule(self, event_handler: object, path: str, recursive: bool = False) -> None:
        self.scheduled.append((event_handler, path, recursive))

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def join(self, timeout: float | None = None) -> None:
        self.joined = True


class _ScheduleFailingObserver(_FakeObserver):
    """Observer whose first schedule attempt fails before monitoring starts."""

    def schedule(self, event_handler: object, path: str, recursive: bool = False) -> None:
        super().schedule(event_handler, path, recursive)
        raise OSError("watch registration failed")


class _StartFailingObserver(_FakeObserver):
    """Observer whose startup fails after a recursive watch is registered."""

    def __init__(self, message: str = "watch startup failed") -> None:
        super().__init__()
        self._message = message

    def start(self) -> None:
        raise OSError(self._message)


class _CapacityFailingObserver(_FakeObserver):
    """Observer whose registration reports exhausted watch capacity."""

    def schedule(self, event_handler: object, path: str, recursive: bool = False) -> None:
        super().schedule(event_handler, path, recursive)
        raise OSError(errno.ENOSPC, "watch limit reached")


class _InotifyInstanceLimitObserver(_FakeObserver):
    """Observer whose startup exhausts the host inotify-instance limit."""

    def start(self) -> None:
        raise OSError(errno.EMFILE, "inotify instance limit reached")


class _StopFailingObserver(_FakeObserver):
    """Observer that raises during lifecycle release after joining."""

    def stop(self) -> None:
        self.stopped = True
        raise OSError("watch release failed")


class _ScheduleAndStopFailingObserver(_ScheduleFailingObserver):
    """Observer whose failed startup rollback also fails during stop."""

    def stop(self) -> None:
        self.stopped = True
        raise OSError("startup rollback release failed")


# ---------------------------------------------------------------------------
# AC-01: exactly one recursive root watch, every classifier config
# ---------------------------------------------------------------------------


def test_start_schedules_single_recursive_root_watch() -> None:
    """With the default classifier, ``start()`` schedules exactly
    one watchdog watch on the workspace root with ``recursive=True``.
    The fseventsd footprint is the minimal single recursive stream."""
    fake = _FakeObserver()

    monitor = _monitor(
        Path("/ws"),
        lambda: fake,
        classifier=WorkspaceChangeClassifier(),
    )
    monitor.start()

    assert len(fake.scheduled) == 1
    _handler, path, recursive = fake.scheduled[0]
    assert path == "/ws"
    assert recursive is True
    assert fake.started is True


def test_start_single_recursive_root_watch_when_classifier_none() -> None:
    """With ``classifier=None`` (default), ``start()`` also schedules
    exactly one recursive watch on the workspace root. The default
    source-only classifier drops Ralph-managed internal paths so internal
    activity never recursively triggers workspace awareness (S-2).
    Parity across configs."""
    fake = _FakeObserver()

    monitor = _monitor(
        Path("/ws"),
        lambda: fake,
        classifier=None,
    )
    monitor.start()

    assert len(fake.scheduled) == 1
    _handler, path, recursive = fake.scheduled[0]
    assert path == "/ws"
    assert recursive is True
    assert fake.started is True
    # The default classifier drops internal paths, counts source paths.
    monitor.dispatch_event(_FakeEvent("/ws/.agent/tmp/foo.py"))
    assert monitor.event_count == 0
    monitor.dispatch_event(_FakeEvent("/ws/src/app.py"))
    assert monitor.event_count == 1


def test_start_failure_enters_live_fallback_and_allows_a_later_watch_registration_retry() -> None:
    """S-2 regression: non-capacity observer failures degrade safely and remain retryable."""
    failing = _ScheduleFailingObserver()
    succeeding = _FakeObserver()
    observers = iter((failing, succeeding))

    monitor = _monitor(
        Path("/ws"),
        lambda: next(observers),
        classifier=WorkspaceChangeClassifier(),
    )

    monitor.start()

    assert monitor.awareness_status["freshness"] == "live_fallback"
    assert monitor.awareness_status["cause"] == "observer_start_failed"
    monitor.start()

    assert len(failing.scheduled) == 1
    assert failing.stopped is True
    assert failing.joined is True
    assert len(succeeding.scheduled) == 1
    assert succeeding.started is True


def test_start_failure_after_registration_enters_live_fallback_and_remains_retryable() -> None:
    """S-2 regression: a post-registration startup failure degrades safely and remains retryable."""
    failing = _StartFailingObserver("watch registration failed")
    succeeding = _FakeObserver()
    observers = iter((failing, succeeding))

    monitor = _monitor(
        Path("/ws"),
        lambda: next(observers),
        classifier=WorkspaceChangeClassifier(),
    )

    monitor.start()

    assert monitor.awareness_status["freshness"] == "live_fallback"
    assert monitor.awareness_status["cause"] == "observer_start_failed"
    monitor.start()

    assert len(failing.scheduled) == 1
    assert failing.stopped is True
    assert failing.joined is True
    assert len(succeeding.scheduled) == 1
    assert succeeding.started is True


def test_workspace_monitor_regression_start_failure_after_registration_releases_watch_for_retry() -> (
    None
):
    """S-5 regression: partial startup does not strand a registered watch."""
    failing = _StartFailingObserver()
    succeeding = _FakeObserver()
    observers = iter((failing, succeeding))

    monitor = _monitor(
        Path("/ws"),
        lambda: next(observers),
        classifier=WorkspaceChangeClassifier(),
    )

    monitor.start()

    assert monitor.awareness_status["freshness"] == "live_fallback"
    assert monitor.awareness_status["cause"] == "observer_start_failed"
    monitor.start()

    assert len(failing.scheduled) == 1
    assert failing.stopped is True
    assert failing.joined is True
    assert len(succeeding.scheduled) == 1
    assert succeeding.started is True


def test_start_rollback_stop_failure_releases_monitor_for_a_later_retry() -> None:
    """S-5 regression: failed startup cleanup cannot strand watch ownership."""
    failing = _ScheduleAndStopFailingObserver()
    succeeding = _FakeObserver()
    observers = iter((failing, succeeding))

    monitor = _monitor(
        Path("/ws"),
        lambda: next(observers),
        classifier=WorkspaceChangeClassifier(),
    )

    monitor.start()

    assert monitor.awareness_status["freshness"] == "live_fallback"
    assert monitor.awareness_status["cause"] == "observer_start_failed"
    monitor.start()

    assert failing.joined is True
    assert len(succeeding.scheduled) == 1
    assert succeeding.started is True


def test_capacity_exhaustion_enters_observable_live_fallback() -> None:
    """S-3: capacity failures are visible and retain bounded live fallback semantics."""
    fake = _CapacityFailingObserver()

    monitor = _monitor(
        Path("/ws"),
        lambda: fake,
        classifier=WorkspaceChangeClassifier(),
    )

    monitor.start()

    assert monitor.awareness_status == {
        "mode": "live_fallback",
        "freshness": "live_fallback",
        "cause": "watch_capacity",
        "automatic_recovery": True,
        "safe_next_action": "Ralph will retry observation on the next workspace lease.",
    }
    assert fake.stopped is True
    assert fake.joined is True


def test_inotify_instance_limit_during_start_enters_live_fallback() -> None:
    """An exhausted inotify-instance limit must not abort agent execution."""
    fake = _InotifyInstanceLimitObserver()

    monitor = _monitor(
        Path("/ws"),
        lambda: fake,
        classifier=WorkspaceChangeClassifier(),
    )

    monitor.start()

    assert monitor.awareness_status["cause"] == "watch_capacity"
    assert fake.stopped is True
    assert fake.joined is True


def test_capacity_exhaustion_updates_shared_awareness_status() -> None:
    """S-3 regression: public status never calls a failed observer current."""
    fake = _CapacityFailingObserver()

    monitor = _monitor(
        Path("/canonical-fallback"),
        lambda: fake,
        classifier=WorkspaceChangeClassifier(),
    )

    monitor.start()

    from ralph.workspace.awareness import awareness_for_workspace

    assert (
        awareness_for_workspace(Path("/canonical-fallback")).snapshot()["freshness"]
        == "live_fallback"
    )
    release_workspace_awareness(Path("/canonical-fallback"))


def test_predicted_watch_capacity_skips_observer_and_updates_shared_awareness() -> None:
    """A predicted capacity breach falls back before constructing an observer."""
    workspace = Path("/synthetic-ws-predicted-capacity")

    monitor = _monitor(
        workspace,
        lambda: pytest.fail("over-budget workspaces must not construct an observer"),
        host_budget=500,
        directory_counter=_fixed_directory_counter(1000),
    )

    try:
        monitor.start()

        assert monitor.awareness_status["freshness"] == "live_fallback"
        assert monitor.awareness_status["cause"] == "watch_capacity_predicted"
        from ralph.workspace.awareness import awareness_for_workspace

        assert awareness_for_workspace(workspace).snapshot()["cause"] == "watch_capacity_predicted"
    finally:
        from ralph.workspace.awareness import release_workspace_awareness

        release_workspace_awareness(workspace)


def test_predicted_watch_capacity_allows_an_under_budget_recursive_watch() -> None:
    """An injected under-budget prediction keeps the existing recursive watch behavior."""
    workspace = Path("/synthetic-ws-predicted-under-budget")
    fake = _FakeObserver()

    monitor = _monitor(
        workspace,
        lambda: fake,
        host_budget=500,
        directory_counter=_fixed_directory_counter(5),
    )

    monitor.start()

    assert monitor.awareness_status["freshness"] == "current"
    assert monitor.awareness_status["cause"] is None
    assert len(fake.scheduled) == 1
    _handler, path, recursive = fake.scheduled[0]
    assert path == str(workspace)
    assert recursive is True
    monitor.stop()


@pytest.mark.parametrize(
    ("directory_count", "live_watch_total"),
    ((500, 0), (100, 450), (None, 0)),
)
def test_predicted_watch_capacity_rejects_boundary_and_unbounded_counts(
    directory_count: int | None,
    live_watch_total: int,
) -> None:
    """The combined per-user prediction rejects equality and unknown over-cap counts."""
    workspace = Path(f"/synthetic-ws-predicted-{directory_count}-{live_watch_total}")

    monitor = _monitor(
        workspace,
        lambda: pytest.fail("over-budget workspaces must not construct an observer"),
        host_budget=500,
        directory_counter=_fixed_directory_counter(directory_count),
        live_watch_total=live_watch_total,
    )

    try:
        monitor.start()

        assert monitor.awareness_status["cause"] == "watch_capacity_predicted"
    finally:
        from ralph.workspace.awareness import release_workspace_awareness

        release_workspace_awareness(workspace)


def test_predicted_watch_capacity_allows_retry_after_larger_budget() -> None:
    """A later lease can schedule after a prior predicted capacity fallback."""
    workspace = Path("/synthetic-ws-predicted-retry")
    fake = _FakeObserver()

    rejected = _monitor(
        workspace,
        lambda: fake,
        host_budget=500,
        directory_counter=_fixed_directory_counter(1000),
    )
    accepted = _monitor(
        workspace,
        lambda: fake,
        host_budget=501,
        directory_counter=_fixed_directory_counter(5),
    )

    rejected.start()
    accepted.start()

    assert rejected.awareness_status["cause"] == "watch_capacity_predicted"
    assert accepted.awareness_status["freshness"] == "current"
    assert len(fake.scheduled) == 1
    _handler, path, recursive = fake.scheduled[0]
    assert path == str(workspace)
    assert recursive is True
    accepted.stop()


def test_start_replay_preserves_one_live_workspace_watch() -> None:
    """S-6 regression: re-starting an active monitor does not add a duplicate watch."""
    fake = _FakeObserver()

    monitor = _monitor(
        Path("/ws"),
        lambda: fake,
        classifier=WorkspaceChangeClassifier(),
    )

    monitor.start()
    monitor.start()

    assert len(fake.scheduled) == 1
    assert fake.started is True


def test_stop_failure_releases_monitor_for_a_later_watch_registration() -> None:
    """S-5 regression: failed release still joins and clears stale watch ownership."""
    failing = _StopFailingObserver()
    succeeding = _FakeObserver()
    observers = iter((failing, succeeding))

    monitor = _monitor(
        Path("/ws"),
        lambda: next(observers),
        classifier=WorkspaceChangeClassifier(),
    )

    monitor.start()
    with pytest.raises(OSError, match="watch release failed"):
        monitor.stop()
    monitor.start()

    assert failing.joined is True
    assert len(succeeding.scheduled) == 1
    assert succeeding.started is True


# ---------------------------------------------------------------------------
# W8: engine-internal classifier boundary
# ---------------------------------------------------------------------------

ENGINE_INTERNAL_PATHS: tuple[str, ...] = (
    ".agent/tmp/foo.py",
    ".agent/raw/bar.log",
    ".agent/ralph-explore/index.sqlite",
    ".git/HEAD",
    ".venv/lib/x.py",
    "node_modules/foo/bar.js",
    "__pycache__/x.pyc",
    ".mypy_cache/x/0.json",
    ".ruff_cache/x/0.json",
    ".pytest_cache/x/v/cache",
    ".agent/artifacts/plan.md",
    "app.log",
    "scratch.tmp",
    "checkpoint.bak",
    "editor.swp",
    "notes~",
    ".agent/completion_seen_run-1.json",
    ".agent/state.db",
    ".agent/state.db-wal",
    ".agent/state.db-shm",
)


def test_engine_internal_paths_classify_as_drop() -> None:
    """S-2: each enumerated engine-internal path is excluded from activity."""
    classifier = WorkspaceChangeClassifier()

    for path in ENGINE_INTERNAL_PATHS:
        kind, weight = classifier.classify(path)
        assert (kind, weight) in {
            (WorkspaceChangeKind.CACHE, 0.0),
            (WorkspaceChangeKind.ARTIFACT, 0.0),
            (WorkspaceChangeKind.LOG, 0.0),
        }


def test_user_source_paths_classify_as_source() -> None:
    """S-2: receipts are run state, not relocatable engine bookkeeping.

    Rule 6 classifies source extensions outside explicit cache/artifact/log
    boundaries as source, so W8 cannot claim universal drop classification.
    """
    classifier = WorkspaceChangeClassifier()

    for path in (
        "src/app.py",
        "README.md",
        "pyproject.toml",
        "tests/test_workspace_watch_scoping.py",
        "docs/guide.md",
    ):
        assert classifier.classify(path) == (WorkspaceChangeKind.SOURCE, 1.0)


# ---------------------------------------------------------------------------
# AC-02: record_event classify-drop parity
# ---------------------------------------------------------------------------


def test_record_event_counts_unchanged_equivalence(tmp_path: Path) -> None:
    """``record_event`` produces identical observable behavior:
    ``src/app.py`` and ``README.md`` count; ``.git/index``,
    ``.venv/lib/x.py``, ``.agent/PLAN.md`` (engine-owned bookkeeping),
    ``.agent/raw/out.log``, ``.agent/artifacts/plan.json``, and
    ``foo.log`` do NOT. ``last_event_at`` advances for the two counted
    events only."""
    monitor = WorkspaceMonitor(
        tmp_path,
        classifier=WorkspaceChangeClassifier(),
    )
    paths = [
        "src/app.py",
        ".agent/PLAN.md",
        ".git/index",
        ".venv/lib/x.py",
        ".agent/raw/out.log",
        ".agent/ralph-explore/index.sqlite",
        ".agent/artifacts/plan.json",
        "README.md",
        "foo.log",
    ]
    for p in paths:
        monitor.record_event(str(tmp_path / p))

    assert monitor.event_count == 2
    expected_files = {
        str(tmp_path / "src/app.py"),
        str(tmp_path / "README.md"),
    }
    assert monitor.changed_files == expected_files
    assert monitor.last_event_at is not None


# ---------------------------------------------------------------------------
# AC-03: production handler wired through the public seam, no dynamic
# per-directory watch scheduling
# ---------------------------------------------------------------------------


def test_dispatched_file_event_counts_via_public_seam() -> None:
    """A file event delivered through the public
    ``monitor.dispatch_event`` seam is counted and adds no new
    ``observer.schedule`` call. Exercises the production handler ->
    ``record_event`` wiring through the public seam (PA-001)."""
    fake = _FakeObserver()

    monitor = _monitor(
        Path("/ws"),
        lambda: fake,
        classifier=WorkspaceChangeClassifier(),
    )
    monitor.start()
    initial_count = len(fake.scheduled)

    monitor.dispatch_event(_FakeEvent("/ws/src/app.py"))

    assert monitor.event_count == 1
    assert "/ws/src/app.py" in monitor.changed_files
    assert len(fake.scheduled) == initial_count


def test_dispatched_outside_workspace_event_is_ignored_at_monitor_ingress() -> None:
    """S-6 regression: events outside the subscribed root do not refresh activity."""
    fake = _FakeObserver()

    monitor = _monitor(
        Path("/ws"),
        lambda: fake,
        classifier=WorkspaceChangeClassifier(),
    )
    monitor.start()

    monitor.dispatch_event(_FakeEvent("/other-project/src/app.py"))

    assert monitor.event_count == 0
    assert monitor.changed_files == set()
    assert monitor.last_event_at is None


def test_dispatched_moved_in_workspace_file_counts_destination_at_monitor_ingress() -> None:
    """S-5 regression: a file moved into the root refreshes workspace activity.

    The observer reports the source outside the root and the destination inside
    it. The monitor must classify the destination through its public dispatch
    seam without adding another recursive watch.
    """
    fake = _FakeObserver()

    monitor = _monitor(
        Path("/ws"),
        lambda: fake,
        classifier=WorkspaceChangeClassifier(),
    )
    monitor.start()
    initial_count = len(fake.scheduled)

    monitor.dispatch_event(_FakeEvent("/other-project/app.py", dest_path="/ws/src/app.py"))

    assert monitor.event_count == 1
    assert monitor.changed_files == {"/ws/src/app.py"}
    assert len(fake.scheduled) == initial_count


def test_dispatched_directory_event_schedules_no_additional_watch() -> None:
    """A created/moved-in directory event delivered through the public
    ``monitor.dispatch_event`` seam produces ZERO additional
    ``observer.schedule`` calls. The recursive root watch already
    covers new subdirectories; no dynamic scheduling remains (AC-03)."""
    fake = _FakeObserver()

    monitor = _monitor(
        Path("/ws"),
        lambda: fake,
        classifier=WorkspaceChangeClassifier(),
    )
    monitor.start()
    initial_count = len(fake.scheduled)

    event = _FakeEvent(
        src_path="/ws/newpkg",
        is_directory=True,
        event_type="created",
    )
    monitor.dispatch_event(event)

    assert len(fake.scheduled) == initial_count


# ---------------------------------------------------------------------------
# The monitor must never observe its own shared-awareness sidecar
# ---------------------------------------------------------------------------


def test_record_event_ignores_the_monitors_own_sidecar_even_when_cache_counts(
    tmp_path: Path,
) -> None:
    """The sidecar is published by ``record_event`` itself; observing that
    publication as a change would feed the monitor its own output forever.
    The guard must hold even for an operator who opted ``cache`` in."""
    seen: list[tuple[WorkspaceChangeKind, float]] = []
    monitor = WorkspaceMonitor(
        tmp_path,
        classifier=WorkspaceChangeClassifier(weights={"cache": 1.0}),
        on_event=lambda kind, weight: seen.append((kind, weight)),
    )
    monitor.record_event(str(tmp_path / ".agent" / ".workspace-awareness.json"))
    monitor.record_event(str(tmp_path / ".agent" / ".workspace-awareness.json.tmp"))

    assert monitor.event_count == 0
    assert seen == []
    assert monitor.last_event_at is None

    monitor.record_event(str(tmp_path / ".agent" / "state.db"))
    assert monitor.event_count == 1, "other cache paths still honour the operator's opt-in"


def test_record_event_drops_the_runners_root_checkpoint_but_not_a_user_file(tmp_path: Path) -> None:
    """The monitor passes its root so ``checkpoint.json`` at the root is engine-owned."""
    monitor = WorkspaceMonitor(
        tmp_path,
        classifier=WorkspaceChangeClassifier(),
        host_budget=8192,
        directory_counter=lambda *_, **__: 0,
        live_watch_total=0,
    )
    monitor.record_event(str(tmp_path / "checkpoint.json"))
    assert monitor.event_count == 0
    monitor.record_event(str(tmp_path / "src" / "checkpoint.json"))
    assert monitor.event_count == 1
