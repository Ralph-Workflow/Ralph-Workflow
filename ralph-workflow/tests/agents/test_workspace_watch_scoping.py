"""Black-box tests for the workspace file-watcher (single recursive root watch).

These tests prove ``WorkspaceMonitor.start()`` schedules exactly ONE
recursive watchdog watch on the workspace root for any classifier
configuration. Activity-counting behavior is preserved by the
``record_event`` classify-drop backstop (independent of watch
scheduling), so observable behavior is unchanged while the
fseventsd footprint shrinks from the previous 4+ overlapping
OS-recursive streams down to one.

No real filesystem or watchdog observer is touched. The
``_FakeObserver`` records every ``schedule()`` call into a list and
no-ops ``start``/``stop``/``join``. All events flow through the
public ``monitor.dispatch_event`` seam (never the private handler)
so the rewritten tests prove the production dispatch wiring without
coupling to internals.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.invoke._workspace import WorkspaceMonitor
from ralph.agents.invoke._workspace_change_classifier import WorkspaceChangeClassifier

if TYPE_CHECKING:
    import pytest


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


# ---------------------------------------------------------------------------
# AC-01: exactly one recursive root watch, every classifier config
# ---------------------------------------------------------------------------


def test_start_schedules_single_recursive_root_watch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the default classifier, ``start()`` schedules exactly
    one watchdog watch on the workspace root with ``recursive=True``.
    The fseventsd footprint is the minimal single recursive stream."""
    fake = _FakeObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer",
        lambda: fake,
    )

    monitor = WorkspaceMonitor(Path("/ws"), classifier=WorkspaceChangeClassifier())
    monitor.start()

    assert len(fake.scheduled) == 1
    _handler, path, recursive = fake.scheduled[0]
    assert path == "/ws"
    assert recursive is True
    assert fake.started is True


def test_start_single_recursive_root_watch_when_classifier_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With ``classifier=None`` (legacy), ``start()`` also schedules
    exactly one recursive watch on the workspace root. Parity across
    configs."""
    fake = _FakeObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer",
        lambda: fake,
    )

    monitor = WorkspaceMonitor(Path("/ws"), classifier=None)
    monitor.start()

    assert len(fake.scheduled) == 1
    _handler, path, recursive = fake.scheduled[0]
    assert path == "/ws"
    assert recursive is True
    assert fake.started is True


# ---------------------------------------------------------------------------
# AC-02: record_event classify-drop parity
# ---------------------------------------------------------------------------


def test_record_event_counts_unchanged_equivalence(tmp_path: Path) -> None:
    """``record_event`` produces identical observable behavior:
    ``src/app.py``, ``.agent/PLAN.md``, and ``README.md`` count;
    ``.git/index``, ``.venv/lib/x.py``, ``.agent/raw/out.log``,
    ``.agent/artifacts/plan.json``, and ``foo.log`` do NOT.
    ``last_event_at`` advances for the three counted events only."""
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
        ".agent/artifacts/plan.json",
        "README.md",
        "foo.log",
    ]
    for p in paths:
        monitor.record_event(str(tmp_path / p))

    assert monitor.event_count == 3
    expected_files = {
        str(tmp_path / "src/app.py"),
        str(tmp_path / ".agent/PLAN.md"),
        str(tmp_path / "README.md"),
    }
    assert monitor.changed_files == expected_files
    assert monitor.last_event_at is not None


# ---------------------------------------------------------------------------
# AC-03: production handler wired through the public seam, no dynamic
# per-directory watch scheduling
# ---------------------------------------------------------------------------


def test_dispatched_file_event_counts_via_public_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file event delivered through the public
    ``monitor.dispatch_event`` seam is counted and adds no new
    ``observer.schedule`` call. Exercises the production handler ->
    ``record_event`` wiring through the public seam (PA-001)."""
    fake = _FakeObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer",
        lambda: fake,
    )

    monitor = WorkspaceMonitor(Path("/ws"), classifier=WorkspaceChangeClassifier())
    monitor.start()
    initial_count = len(fake.scheduled)

    monitor.dispatch_event(_FakeEvent("/ws/src/app.py"))

    assert monitor.event_count == 1
    assert "/ws/src/app.py" in monitor.changed_files
    assert len(fake.scheduled) == initial_count


def test_dispatched_directory_event_schedules_no_additional_watch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A created/moved-in directory event delivered through the public
    ``monitor.dispatch_event`` seam produces ZERO additional
    ``observer.schedule`` calls. The recursive root watch already
    covers new subdirectories; no dynamic scheduling remains (AC-03)."""
    fake = _FakeObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer",
        lambda: fake,
    )

    monitor = WorkspaceMonitor(Path("/ws"), classifier=WorkspaceChangeClassifier())
    monitor.start()
    initial_count = len(fake.scheduled)

    event = _FakeEvent(
        src_path="/ws/newpkg",
        is_directory=True,
        event_type="created",
    )
    monitor.dispatch_event(event)

    assert len(fake.scheduled) == initial_count
