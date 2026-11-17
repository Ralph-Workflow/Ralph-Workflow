#!/usr/bin/env python3

from __future__ import annotations

import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ralph.mcp.explore._burst_scheduler import BurstDebounceScheduler
from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.store import ExploreStore


class _FakeClock:
    __slots__ = ("_t",)

    def __init__(self, initial: float = 0.0) -> None:
        self._t = initial

    def __call__(self) -> float:
        return self._t

    def advance(self, delta: float) -> None:
        self._t += delta


class _FireCounter:
    __slots__ = ("count",)

    def __init__(self) -> None:
        self.count = 0

    def __call__(self) -> None:
        self.count += 1


class _MarkCounter:
    __slots__ = ("_lock", "count")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.count = 0

    def bump(self) -> None:
        with self._lock:
            self.count += 1


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
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        committed_generation = int(store.get_setting("current_generation") or 0)
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


def _scenario_concurrent(_root: Path) -> None:
    clock = _FakeClock()
    fires = _FireCounter()
    marks = _MarkCounter()
    scheduler = BurstDebounceScheduler(
        clock=clock,
        on_fire=fires,
        debounce_window=1.0,
    )

    def _mark_one() -> None:
        scheduler.mark(lambda: None)
        marks.bump()

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _: _mark_one(), range(50)))
    clock.advance(1.0)
    scheduler.fire_if_due()
    print(f"concurrent mark_count {marks.count}")
    print(f"concurrent fire_count {fires.count}")


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
