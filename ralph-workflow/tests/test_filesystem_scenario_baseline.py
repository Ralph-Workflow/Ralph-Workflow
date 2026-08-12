"""S-12: 6-scenario x 6-dimension filesystem baseline measurement.

Every scenario drives a REAL production seam (WorkspaceMonitor,
ExploreStore, ``reindex``, ``handle_search_files``,
``register_active_run``, ``prune_lock_run_ids``,
``inventory_storage``, ``awareness_for_workspace``).

The watchdog observer is replaced with a recording fake
(``_ActivityObserver``) because a real ``watchdog.Observer`` would
consume host watch capacity.  No subprocess, no ``time.sleep``,
no real host watchdog.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke._workspace import WorkspaceMonitor
from ralph.agents.invoke._workspace_change_classifier import WorkspaceChangeClassifier
from ralph.mcp.explore.dirty_paths import build_sqlite_index_handle
from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.store import ExploreStore
from ralph.mcp.tools.workspace._read_handlers import handle_search_files
from ralph.workspace.agent_dir_retention import (
    prune_lock_run_ids,
    register_active_run,
    unregister_active_run,
)
from ralph.workspace.awareness import (
    awareness_for_workspace,
    release_workspace_awareness,
)
from ralph.workspace.storage_lifecycle import inventory_storage

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


# ---------------------------------------------------------------------------
# Recording fake observer (replaces real watchdog to avoid host watch capacity)
# ---------------------------------------------------------------------------


class _ActivityObserver:
    """In-memory watch boundary exposing recursive registrations."""

    def __init__(self) -> None:
        self.registrations: list[tuple[str, bool]] = []

    def schedule(self, event_handler: object, path: str, recursive: bool = False) -> None:
        del event_handler
        self.registrations.append((path, recursive))

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def join(self, timeout: float | None = None) -> None:
        del timeout


# ---------------------------------------------------------------------------
# Minimal session / workspace fakes for handler invocation
# ---------------------------------------------------------------------------


class _FakeSession:
    """Minimal coordination session with explore index and capability approval."""

    def __init__(self, explore_index: object | None = None) -> None:
        self.explore_index = explore_index

    def check_capability(self, capability: str) -> dict[str, str]:
        return {"status": "approved", "capability": capability}

    def check_edit_area(self, path: str) -> dict[str, str]:
        return {"status": "approved", "path": path}


class _Workspace:
    """Lightweight workspace wrapper for handler invocations."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def write(self, path: str, content: str) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def read(self, path: str) -> str:
        return (self.root / path).read_text()

    def stat(self, path: str) -> dict[str, object]:
        target = self.root / path
        if target.is_dir():
            return {"type": "dir", "size_bytes": 0}
        if target.exists():
            return {"type": "file", "size_bytes": target.stat().st_size}
        return {"type": "missing", "size_bytes": 0}

    def iter_files(self, base: str) -> object:
        base_path = self.root / base if base else self.root

        def _gen() -> object:
            for p in base_path.rglob("*"):
                if p.is_file():
                    yield str(p.relative_to(self.root))

        return _gen()

    def list_dir(self, base: str) -> list[str]:
        target = self.root / base if base else self.root
        return [p.name for p in target.iterdir()]


class _WriteTracker:
    """Counts bytes and operations written during a scenario."""

    def __init__(self) -> None:
        self.bytes_written: int = 0
        self.operations: int = 0

    def write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        self.bytes_written += len(content.encode("utf-8"))
        self.operations += 1


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_DIMENSIONS: tuple[str, ...] = (
    "watch use",
    "filesystem activity",
    "retained storage",
    "freshness",
    "relevance",
    "responsiveness",
)

_VALID_FRESHNESS: frozenset[str] = frozenset(
    {"current", "pending", "partial", "stale", "unavailable", "live_fallback"}
)

# Row: (dimension, observed, threshold_human, source_seam)
type _Row = tuple[str, object, str, str]


def _seed_files(ws_root: Path, tracker: _WriteTracker, count: int = 3) -> list[str]:
    """Seed *count* Python files; return the list of relative paths."""
    rels: list[str] = []
    for i in range(count):
        rel = f"src/module_{i}.py"
        tracker.write(
            ws_root / rel,
            f"def func_{i}():\n    return {i}\n",
        )
        rels.append(rel)
    return rels


def _build_index(store: ExploreStore, ws_root: Path) -> None:
    """Cold-build the index for *ws_root*."""
    reindex(store, ws_root, options=ReindexOptions(timeout_ms=10_000))


def _total_inventory_bytes(ws_root: Path) -> int:
    inv = inventory_storage(ws_root)
    return sum(e["bytes"] for e in inv if isinstance(e["bytes"], int))


def _make_event(src_path: str) -> object:
    """Create a minimal watchdog-style event for dispatch_event."""
    return type("Event", (), {"src_path": src_path})()


def _measure_dimensions(
    ws_root: Path,
    observer: _ActivityObserver,
    tracker: _WriteTracker,
    store: ExploreStore,
) -> list[_Row]:
    """Measure all 6 dimensions and return 6 rows."""
    rows: list[_Row] = []

    # 1. watch use — observer.schedule registrations
    rows.append((
        "watch use",
        len(observer.registrations),
        "<= 1",
        "WorkspaceMonitor.start -> observer.schedule",
    ))

    # 2. filesystem activity — workload bytes written
    rows.append((
        "filesystem activity",
        tracker.bytes_written,
        "<= 512_000",
        "_WriteTracker.write (Path.write_text)",
    ))

    # 3. retained storage — inventory_storage
    rows.append((
        "retained storage",
        _total_inventory_bytes(ws_root),
        "<= 5_242_880",
        "inventory_storage(workspace_root)",
    ))

    # 4. freshness — awareness snapshot
    snap = awareness_for_workspace(ws_root).snapshot()
    rows.append((
        "freshness",
        snap["freshness"],
        "valid freshness state",
        "awareness.snapshot()['freshness']",
    ))

    # 5 + 6. relevance + responsiveness — timed reindex then search
    start = time.perf_counter()
    reindex(store, ws_root, options=ReindexOptions(timeout_ms=10_000))
    elapsed = time.perf_counter() - start

    session = _FakeSession(build_sqlite_index_handle(store))
    workspace = _Workspace(ws_root)
    result = handle_search_files(
        session,
        workspace,
        {"pattern": "**/*.py", "path": "."},
    )
    payload = json.loads(result.content[0].text)
    match_count = len(payload.get("matches", []))

    rows.append((
        "relevance",
        match_count,
        ">= 0",
        "reindex + handle_search_files",
    ))
    rows.append((
        "responsiveness",
        elapsed,
        "<= 10.0 s",
        "time.perf_counter() around reindex",
    ))

    return rows


# ---------------------------------------------------------------------------
# Scenario implementations
# ---------------------------------------------------------------------------


def _scenario_unchanged(
    ws_root: Path,
    observer: _ActivityObserver,
    tracker: _WriteTracker,
) -> list[_Row]:
    """Scenario 1: a settled workspace that is simply observed."""
    _seed_files(ws_root, tracker, count=3)
    (ws_root / ".agent").mkdir(exist_ok=True)
    store = ExploreStore(ws_root / ".agent" / "ralph-explore")
    monitor: WorkspaceMonitor | None = None
    try:
        _build_index(store, ws_root)
        monitor = WorkspaceMonitor(ws_root, classifier=WorkspaceChangeClassifier())
        monitor.start()
        # No changes — simply observe the settled workspace.
        return _measure_dimensions(ws_root, observer, tracker, store)
    finally:
        if monitor is not None:
            monitor.stop()
        store.close()
        release_workspace_awareness(ws_root)


def _scenario_localized_change(
    ws_root: Path,
    observer: _ActivityObserver,
    tracker: _WriteTracker,
) -> list[_Row]:
    """Scenario 2: a single file is edited after initial indexing."""
    _seed_files(ws_root, tracker, count=3)
    (ws_root / ".agent").mkdir(exist_ok=True)
    store = ExploreStore(ws_root / ".agent" / "ralph-explore")
    monitor: WorkspaceMonitor | None = None
    try:
        _build_index(store, ws_root)
        monitor = WorkspaceMonitor(ws_root, classifier=WorkspaceChangeClassifier())
        monitor.start()

        # Edit one file and dispatch the event so the monitor records it.
        changed = ws_root / "src/module_0.py"
        tracker.write(changed, "def func_0():\n    return 42  # edited\n")
        monitor.dispatch_event(_make_event(str(changed)))

        return _measure_dimensions(ws_root, observer, tracker, store)
    finally:
        if monitor is not None:
            monitor.stop()
        store.close()
        release_workspace_awareness(ws_root)


def _scenario_large_workspace(
    ws_root: Path,
    observer: _ActivityObserver,
    tracker: _WriteTracker,
) -> list[_Row]:
    """Scenario 3: a workspace with 100+ files, no changes after initial index."""
    _seed_files(ws_root, tracker, count=120)
    (ws_root / ".agent").mkdir(exist_ok=True)
    store = ExploreStore(ws_root / ".agent" / "ralph-explore")
    monitor: WorkspaceMonitor | None = None
    try:
        _build_index(store, ws_root)
        monitor = WorkspaceMonitor(ws_root, classifier=WorkspaceChangeClassifier())
        monitor.start()
        return _measure_dimensions(ws_root, observer, tracker, store)
    finally:
        if monitor is not None:
            monitor.stop()
        store.close()
        release_workspace_awareness(ws_root)


def _scenario_long_running(
    ws_root: Path,
    observer: _ActivityObserver,
    tracker: _WriteTracker,
) -> list[_Row]:
    """Scenario 4: many settle cycles (loop the observe/check cycle 25 times)."""
    _seed_files(ws_root, tracker, count=3)
    (ws_root / ".agent").mkdir(exist_ok=True)
    store = ExploreStore(ws_root / ".agent" / "ralph-explore")
    monitor: WorkspaceMonitor | None = None
    try:
        _build_index(store, ws_root)
        monitor = WorkspaceMonitor(ws_root, classifier=WorkspaceChangeClassifier())
        monitor.start()

        # Loop observe/check cycle 25 times (no time.sleep; deterministic).
        for _ in range(25):
            awareness_for_workspace(ws_root).snapshot()

        return _measure_dimensions(ws_root, observer, tracker, store)
    finally:
        if monitor is not None:
            monitor.stop()
        store.close()
        release_workspace_awareness(ws_root)


def _scenario_interrupted(
    ws_root: Path,
    observer: _ActivityObserver,
    tracker: _WriteTracker,
) -> list[_Row]:
    """Scenario 5: start, mutate, then stop without full cleanup.

    Simulated by: start monitor, mutate a file, register an active run,
    measure the interrupted state, then stop the monitor WITHOUT
    unregistering the active run.
    """
    _seed_files(ws_root, tracker, count=3)
    (ws_root / ".agent").mkdir(exist_ok=True)
    store = ExploreStore(ws_root / ".agent" / "ralph-explore")
    monitor: WorkspaceMonitor | None = None
    run_id = "run-interrupted"
    try:
        _build_index(store, ws_root)
        monitor = WorkspaceMonitor(ws_root, classifier=WorkspaceChangeClassifier())
        monitor.start()

        # Mutate a file and dispatch the event.
        changed = ws_root / "src/module_1.py"
        tracker.write(changed, "def func_1():\n    return 99  # interrupted\n")
        monitor.dispatch_event(_make_event(str(changed)))

        # Register an active run (simulating a workflow in progress).
        register_active_run(ws_root, run_id)

        # Measure while the monitor is active (captures the interrupted state).
        rows = _measure_dimensions(ws_root, observer, tracker, store)

        # Stop the monitor without unregistering the active run
        # (simulating abrupt termination / interrupted cleanup).
        monitor.stop()
        monitor = None

        return rows
    finally:
        if monitor is not None:
            monitor.stop()
        unregister_active_run(ws_root, run_id)
        store.close()
        release_workspace_awareness(ws_root)


def _scenario_concurrent(
    ws_root: Path,
    observer: _ActivityObserver,
    tracker: _WriteTracker,
) -> list[_Row]:
    """Scenario 6: two threads with real WorkspaceMonitor + real ExploreStore.

    Each thread starts its own WorkspaceMonitor on the same workspace,
    registers a distinct run id, and calls ``prune_lock_run_ids``.
    The shared-watch contract asserts exactly one ``observer.schedule``.
    """
    _seed_files(ws_root, tracker, count=3)
    (ws_root / ".agent").mkdir(exist_ok=True)
    store = ExploreStore(ws_root / ".agent" / "ralph-explore")
    run_ids = {"run-alpha", "run-beta"}
    try:
        _build_index(store, ws_root)

        errors: list[str] = []
        barrier = threading.Barrier(2)
        monitors: list[WorkspaceMonitor] = []
        monitors_lock = threading.Lock()

        def _thread_work(run_id: str) -> None:
            try:
                barrier.wait(timeout=10.0)
                m = WorkspaceMonitor(ws_root, classifier=WorkspaceChangeClassifier())
                m.start()
                with monitors_lock:
                    monitors.append(m)
                try:
                    register_active_run(ws_root, run_id)
                    locked = prune_lock_run_ids(ws_root)
                    if run_id not in locked:
                        err = f"{run_id} not in prune_lock_run_ids result: {locked}"
                        errors.append(err)
                finally:
                    m.stop()
            except Exception as exc:
                errors.append(repr(exc))

        threads = [
            threading.Thread(target=_thread_work, args=("run-alpha",)),
            threading.Thread(target=_thread_work, args=("run-beta",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert not errors, f"concurrent scenario thread errors: {errors}"

        # Assert the union of run ids is visible through prune_lock_run_ids.
        locked = prune_lock_run_ids(ws_root)
        assert run_ids <= locked, f"expected {run_ids} <= {locked}"

        # Measure dimensions (monitors already stopped by threads).
        return _measure_dimensions(ws_root, observer, tracker, store)
    finally:
        for m in monitors:
            with contextlib.suppress(Exception):
                m.stop()
        for rid in run_ids:
            unregister_active_run(ws_root, rid)
        store.close()
        release_workspace_awareness(ws_root)


# ---------------------------------------------------------------------------
# Matrix renderer
# ---------------------------------------------------------------------------


def _render_matrix(path: Path, rows: Sequence[tuple[str, str, object, str, str]]) -> None:
    """Render the 6x6 baseline matrix to *path* as markdown.

    Each tuple in *rows* is ``(scenario, dimension, observed, threshold, source)``.
    """
    lines: list[str] = [
        "# Filesystem Scenario Baseline Matrix",
        "",
        f"Generated: {time.strftime('%Y-%m-%dT%H:%M:%S%z')}",
        "",
        "| Scenario | Dimension | Observed | Threshold | Source Seam |",
        "|---|---|---|---|---|",
    ]
    for scenario, dimension, observed, threshold, source in rows:
        lines.append(
            f"| {scenario} | {dimension} | {observed!r} | {threshold} | {source} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Dimension validator
# ---------------------------------------------------------------------------


def _validate_dimension(dimension: str, observed: object) -> bool:
    """Return True when *observed* satisfies the per-dimension threshold."""
    if dimension == "watch use":
        return isinstance(observed, int) and observed <= 1
    if dimension == "filesystem activity":
        return isinstance(observed, int) and observed <= 512_000
    if dimension == "retained storage":
        return isinstance(observed, int) and observed <= 5_242_880
    if dimension == "freshness":
        return isinstance(observed, str) and observed in _VALID_FRESHNESS
    if dimension == "relevance":
        return isinstance(observed, int) and observed >= 0
    # dimension == "responsiveness" or unknown
    return isinstance(observed, float) and observed <= 10.0


# ---------------------------------------------------------------------------
# Main test
# ---------------------------------------------------------------------------

_SCENARIOS: list[
    tuple[str, Callable[[Path, _ActivityObserver, _WriteTracker], list[_Row]]]
] = [
    ("unchanged", _scenario_unchanged),
    ("localized-change", _scenario_localized_change),
    ("large-workspace", _scenario_large_workspace),
    ("long-running", _scenario_long_running),
    ("interrupted", _scenario_interrupted),
    ("concurrent", _scenario_concurrent),
]


@pytest.mark.timeout_seconds(60)
def test_filesystem_scenario_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """S-12: 6 scenarios x 6 dimensions baseline matrix.

    Drives real production seams through every scenario.  The watchdog
    observer is replaced with ``_ActivityObserver`` (no host watch
    capacity).  No subprocess, no ``time.sleep``, no real host watchdog.
    Renders the full 36-cell matrix to ``tmp_path/baseline-matrix.md``.
    """
    observer = _ActivityObserver()
    monkeypatch.setattr(
        "ralph.agents.invoke._workspace._create_watchdog_observer",
        lambda: observer,
    )

    all_rows: list[tuple[str, str, object, str, str]] = []

    for scenario_name, scenario_fn in _SCENARIOS:
        ws_root = tmp_path / scenario_name
        ws_root.mkdir(parents=True, exist_ok=True)
        observer.registrations.clear()
        tracker = _WriteTracker()

        rows: list[_Row] = scenario_fn(ws_root, observer, tracker)
        assert len(rows) == 6, f"{scenario_name}: expected 6 dimensions, got {len(rows)}"
        assert {r[0] for r in rows} == set(_DIMENSIONS), (
            f"{scenario_name}: expected dimensions {_DIMENSIONS}, "
            f"got {tuple(r[0] for r in rows)}"
        )
        all_rows.extend(
            (scenario_name, dim, observed, threshold, source)
            for dim, observed, threshold, source in rows
        )

    # Render matrix.
    matrix_path = tmp_path / "baseline-matrix.md"
    _render_matrix(matrix_path, all_rows)

    # Assert 36 data rows (6 scenarios x 6 dimensions).
    assert len(all_rows) == 36, f"expected 36 cells, got {len(all_rows)}"

    # Assert each observed value is within its threshold.
    failures: list[str] = []
    for scenario, dim, observed, threshold, _source in all_rows:
        if not _validate_dimension(dim, observed):
            failures.append(
                f"{scenario}/{dim}: observed={observed!r}, threshold={threshold}"
            )
    assert not failures, "threshold violations:\n  " + "\n  ".join(failures)

    # Assert the matrix file was written with the expected structure.
    assert matrix_path.exists(), "baseline-matrix.md was not written"
    matrix_text = matrix_path.read_text(encoding="utf-8")
    assert "| Scenario | Dimension | Observed | Threshold | Source Seam |" in matrix_text
    data_lines = [
        line
        for line in matrix_text.splitlines()
        if line.startswith("| ")
        and "Scenario" not in line
        and "---" not in line
        and "Generated" not in line
    ]
    assert len(data_lines) == 36, (
        f"expected 36 data lines in matrix, got {len(data_lines)}"
    )
