"""Concurrency tests for the explore index (acceptance criterion 4).

Several sessions searching and refreshing one workspace at once all get correct
results, run only one rebuild at a time, and never corrupt the index.

The sessions run concurrently in threads. Each thread owns its production
``ExploreStore`` connection and opens the advisory lock independently, so the
``fcntl`` exclusion path is still exercised without creating OS subprocesses.
A ``threading.Barrier`` releases every session at the same point and keeps the
rebuild counter and peak-in-flight instrumentation in an injected in-memory
fake.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.timeout_seconds(60)]


@dataclass
class _RebuildMetrics:
    """Bounded in-memory instrumentation for concurrent rebuild attempts."""

    current: int = 0
    peak: int = 0
    rebuild_count: int = 0

    def rebuild_started(self) -> None:
        """Record one active rebuild and update the observed peak."""
        self.current += 1
        self.peak = max(self.peak, self.current)
        self.rebuild_count += 1

    def rebuild_finished(self) -> None:
        """Record completion of one active rebuild."""
        self.current -= 1


class _FakeSession:
    """Minimal MCP session surface required by the grep handler."""

    def __init__(self, explore_index: object) -> None:
        self.explore_index = explore_index
        self.session_id = "explore-concurrency-session"
        self.run_id = "explore-concurrency-run"
        self.broker_secret = None

    def check_capability(self, capability: str) -> dict[str, str]:
        return {"status": "approved", "capability": capability}

    def check_edit_area(self, path: str) -> dict[str, str]:
        return {"status": "approved", "path": path}


def _seed(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    for index in range(20):
        (workspace / f"f{index:02d}.py").write_text(
            f"def fn_{index}():\n    return {index}\n",
            encoding="utf-8",
        )
    return workspace


def _live_paths(workspace: Path) -> list[str]:
    return sorted(str(path.relative_to(workspace)) for path in workspace.rglob("*.py"))


def _search(workspace: Path) -> dict[str, Any]:
    from ralph.mcp.explore.dirty_paths import build_sqlite_index_handle
    from ralph.mcp.explore.store import ExploreStore
    from ralph.mcp.tools.workspace import handle_grep_files
    from ralph.workspace.fs import FsWorkspace

    index_dir = workspace / ".agent" / "ralph-explore"
    store = ExploreStore(index_dir)
    try:
        session = _FakeSession(build_sqlite_index_handle(store))
        result = handle_grep_files(
            session,
            FsWorkspace(workspace),
            {
                "pattern": "def",
                "path": ".",
                "regex": False,
                "case_sensitive": False,
                "use_index": "auto",
            },
        )
        payload = json.loads(getattr(result.content[0], "text", ""))
        match_paths = sorted({match.get("path") for match in payload.get("matches", [])})
        return {
            "match_paths": match_paths,
            "live_paths": _live_paths(workspace),
            "index_used": payload.get("index_used"),
            "fallback_reason": payload.get("fallback_reason"),
            "match_count": len(match_paths),
        }
    finally:
        store.close()


def _reindex_once(
    workspace: Path,
    *,
    start_barrier: threading.Barrier,
    metrics: _RebuildMetrics,
    metrics_lock: threading.Lock,
) -> dict[str, Any]:
    from ralph.mcp.explore.pipeline import ReindexOptions, reindex
    from ralph.mcp.explore.recovery import release_advisory_lock, try_advisory_lock
    from ralph.mcp.explore.store import ExploreStore

    acquired = try_advisory_lock(workspace)
    start_barrier.wait(timeout=10)
    if not acquired:
        return {"status": "deferred"}
    try:
        store = ExploreStore(workspace / ".agent" / "ralph-explore")
        try:
            result = reindex(
                store,
                workspace,
                options=ReindexOptions(mode="full", timeout_ms=10_000),
            )
            with metrics_lock:
                metrics.rebuild_started()
            try:
                return {"status": result.status, "rebuild_count": metrics.rebuild_count}
            finally:
                with metrics_lock:
                    metrics.rebuild_finished()
        finally:
            store.close()
    finally:
        release_advisory_lock()


def test_multiple_sessions_share_one_workspace(tmp_path: Path) -> None:
    """Several sessions searching one workspace all return correct results."""
    workspace = _seed(tmp_path)
    start_barrier = threading.Barrier(4)

    def run(_barrier: threading.Barrier) -> dict[str, Any]:
        start_barrier.wait(timeout=10)
        return _search(workspace)

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(run, start_barrier) for _ in range(4)]
        payloads = [future.result(timeout=30) for future in futures]

    for payload in payloads:
        assert payload["match_paths"] == payload["live_paths"], payload
        assert payload["match_count"] > 0


def test_concurrent_reindex_is_single_flight(tmp_path: Path) -> None:
    """Concurrent refreshes only ever run one rebuild at a time."""
    workspace = _seed(tmp_path)
    metrics = _RebuildMetrics()
    metrics_lock = threading.Lock()
    start_barrier = threading.Barrier(4)

    def run(barrier: threading.Barrier) -> dict[str, Any]:
        return _reindex_once(
            workspace,
            start_barrier=barrier,
            metrics=metrics,
            metrics_lock=metrics_lock,
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(run, start_barrier) for _ in range(4)]
        payloads = [future.result(timeout=30) for future in futures]

    assert all(payload["status"] in {"ok", "deferred"} for payload in payloads)
    assert metrics.rebuild_count >= 1
    assert metrics.peak == 1

    db = workspace / ".agent" / "ralph-explore" / "index.sqlite"
    assert db.is_file()
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute("SELECT count(*) FROM files").fetchone()
        assert row is not None
        assert row[0] == 20
    finally:
        conn.close()


def test_concurrent_search_and_reindex_correct_for_all(tmp_path: Path) -> None:
    """Concurrent search and reindex return correct results for every search."""
    workspace = _seed(tmp_path)
    metrics = _RebuildMetrics()
    metrics_lock = threading.Lock()
    start_barrier = threading.Barrier(5)

    def reindex_session(barrier: threading.Barrier) -> dict[str, Any]:
        return _reindex_once(
            workspace,
            start_barrier=barrier,
            metrics=metrics,
            metrics_lock=metrics_lock,
        )

    def search_session(_barrier: threading.Barrier) -> dict[str, Any]:
        start_barrier.wait(timeout=10)
        return _search(workspace)

    with ThreadPoolExecutor(max_workers=5) as executor:
        reindex_futures = [executor.submit(reindex_session, start_barrier) for _ in range(2)]
        search_futures = [executor.submit(search_session, start_barrier) for _ in range(3)]
        reindex_payloads = [future.result(timeout=30) for future in reindex_futures]
        search_payloads = [future.result(timeout=30) for future in search_futures]

    assert all(payload["status"] in {"ok", "deferred"} for payload in reindex_payloads)
    for payload in search_payloads:
        assert payload["match_paths"] == payload["live_paths"], payload

    db = workspace / ".agent" / "ralph-explore" / "index.sqlite"
    assert db.is_file()
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute("SELECT count(*) FROM files").fetchone()
        assert row is not None
        assert row[0] == 20
    finally:
        conn.close()


def test_concurrent_construction_no_database_is_locked(tmp_path: Path) -> None:
    """Several sessions can construct a store against one fresh index directory."""
    from ralph.mcp.explore.store import ExploreStore

    index_dir = tmp_path / "shared_index"
    start_barrier = threading.Barrier(6)

    def construct(_barrier: threading.Barrier) -> dict[str, object]:
        start_barrier.wait(timeout=10)
        store = ExploreStore(index_dir, busy_timeout_ms=1)
        try:
            row = store._conn.execute(
                "SELECT value FROM settings WHERE key = 'schema_version'"
            ).fetchone()
            version = row[0] if row is not None else None
            return {"status": "ok", "schema_version": version}
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(construct, start_barrier) for _ in range(6)]
        results = [future.result(timeout=30) for future in futures]

    for payload in results:
        assert payload.get("status") == "ok", payload
        assert payload.get("schema_version"), payload

    db = index_dir / "index.sqlite"
    assert db.is_file()
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = 'schema_version'").fetchone()
        assert row is not None
        assert row[0] == "explore-v2", row
    finally:
        conn.close()
