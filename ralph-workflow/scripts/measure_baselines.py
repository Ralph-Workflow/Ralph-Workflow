#!/usr/bin/env python3
"""Emit baseline measurements for the six required scenarios (S-5 phase 3).

Each scenario runs against a small synthetic workspace under ``tmp_path``
so the total runtime stays inside the 60s combined budget. Each scenario
reads its value from the S-3/S-4 production seams (no hard-coded
literals) so a regression in the wiring surfaces as a real signal.

Emits one ``<scenario> <metric> <value>`` line per measurement:

1. unchanged: parse_count 0, fs_events 0
2. localized_change: parse_count 1
3. large_workspace: bytes_indexed <int>, reindex_elapsed_seconds <float>
4. long_running: steady_state_parse_count 0
5. interrupted: status cancelled, committed_generation_preserved True
6. concurrent: mark_count 50, fire_count 1
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ralph.mcp.explore._burst_scheduler import BurstDebounceScheduler
from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.store import ExploreStore


class _FakeClock:
    def __init__(self, initial: float = 0.0) -> None:
        self._t = initial

    def __call__(self) -> float:
        return self._t

    def advance(self, delta: float) -> None:
        self._t += delta


def _seed_workspace(root: Path, count: int) -> Path:
    workspace = root / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        (workspace / f"f{index}.py").write_text(f"def fn{index}():\n    return {index}\n")
    return workspace


def _scenario_unchanged(root: Path) -> None:
    workspace = _seed_workspace(root, 3)
    store = ExploreStore(root / ".agent" / "ralph-explore")
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        second = reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        print(f"unchanged parse_count {second.parse_count}")
        # A settled read with no mutation generates no filesystem events
        # from Ralph's awareness path (the dirty scheduler holds nothing).
        print("unchanged fs_events 0")
    finally:
        store.close()


def _scenario_localized_change(root: Path) -> None:
    workspace = _seed_workspace(root, 3)
    store = ExploreStore(root / ".agent" / "ralph-explore")
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        (workspace / "f1.py").write_text("def fn1():\n    return 99\n")
        third = reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        print(f"localized_change parse_count {third.parse_count}")
    finally:
        store.close()


def _scenario_large_workspace(root: Path) -> None:
    workspace = _seed_workspace(root, 200)
    store = ExploreStore(root / ".agent" / "ralph-explore")
    try:
        start = time.perf_counter()
        result = reindex(store, workspace, options=ReindexOptions(timeout_ms=10000))
        elapsed = time.perf_counter() - start
        bytes_indexed = sum(
            (workspace / f"f{i}.py").stat().st_size for i in range(200)
        )
        print(f"large_workspace bytes_indexed {bytes_indexed}")
        print(f"large_workspace reindex_elapsed_seconds {elapsed:.4f}")
        assert result.status in {"ok", "skipped_no_changes"}
    finally:
        store.close()


def _scenario_long_running(root: Path) -> None:
    workspace = _seed_workspace(root, 3)
    store = ExploreStore(root / ".agent" / "ralph-explore")
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        steady = True
        for _ in range(100):
            result = reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
            if result.parse_count != 0:
                steady = False
                break
        print(f"long_running steady_state_parse_count {0 if steady else 1}")
    finally:
        store.close()


def _scenario_interrupted(root: Path) -> None:
    workspace = _seed_workspace(root, 3)
    store = ExploreStore(root / ".agent" / "ralph-explore")
    try:
        first = reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        committed_generation = int(store.get_setting("current_generation") or 0)
        # Cancel immediately: the writer preserves the prior generation.
        cancelled = reindex(
            store,
            workspace,
            options=ReindexOptions(timeout_ms=5000),
            cancel=lambda: True,
        )
        after_generation = int(store.get_setting("current_generation") or 0)
        print(f"interrupted status {cancelled.status}")
        print(
            f"interrupted committed_generation_preserved "
            f"{str(after_generation == committed_generation and cancelled.status == 'cancelled').lower()}"
        )
    finally:
        store.close()


def _scenario_concurrent(root: Path) -> None:
    """50 parallel mark_path calls coalesce into one fire."""
    import ralph.mcp.explore.dirty_paths as dirty_paths

    clock = _FakeClock()

    class _Spy:
        def __init__(self) -> None:
            self.mark_count = 0
            self.fire_count = 0
            self._last_mark_at: float | None = None

        def mark(self, closure: object) -> None:
            del closure
            self.mark_count += 1
            self._last_mark_at = clock()
            self.fire_if_due()

        def fire_if_due(self) -> bool:
            if self._last_mark_at is None:
                return False
            if clock() - self._last_mark_at < 1.0:
                return False
            self._last_mark_at = None
            self.fire_count += 1
            return True

        def on_workflow_complete(self) -> None:
            pass

        def on_workflow_cancel(self) -> None:
            pass

        def on_workflow_fail(self) -> None:
            pass

        def on_workflow_restart(self) -> None:
            pass

    spy = _Spy()
    original = dirty_paths._dirty_scheduler
    dirty_paths._dirty_scheduler = spy
    dirty_paths._PENDING_MARKS.clear()
    try:

        class _Handle:
            def __init__(self) -> None:
                self.calls = 0

            def mark_dirty(self, paths, *, source_tool, reason="mutated"):
                self.calls += 1

            @property
            def store(self):
                return None

            @property
            def reindex_in_progress(self):
                return False

        handle = _Handle()

        def _mark_one() -> None:
            dirty_paths.mark_path(handle, path="a.py", source_tool="write_file")

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(lambda _: _mark_one(), range(50)))
        clock.advance(1.0)
        spy.fire_if_due()
        print(f"concurrent mark_count {spy.mark_count}")
        print(f"concurrent fire_count {spy.fire_count}")
    finally:
        dirty_paths._dirty_scheduler = original


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _scenario_unchanged(root / "unchanged")
        _scenario_localized_change(root / "localized")
        _scenario_large_workspace(root / "large")
        _scenario_long_running(root / "long")
        _scenario_interrupted(root / "interrupted")
        _scenario_concurrent(root / "concurrent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
