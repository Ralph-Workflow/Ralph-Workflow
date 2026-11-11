from __future__ import annotations

import json
import sqlite3
import stat
import time
from pathlib import Path
from typing import TYPE_CHECKING

import ralph.mcp.explore.recovery as recovery_module
from ralph.mcp.explore.handlers import (
    ExploreIndex,
    build_explore_index,
    handle_ralph_index_status,
)
from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.recovery import (
    build_scheduler,
    run_pending_recovery,
    run_recovery_action,
)
from ralph.mcp.explore.serving import CANONICAL_REASON_CODES
from ralph.mcp.explore.store import ExploreStore
from ralph.mcp.tools.workspace._grep_handlers import handle_grep_files

if TYPE_CHECKING:
    import pytest


class _FakeSession:
    session_id = "fault"
    run_id = "fault"
    broker_secret: str | None = None

    def __init__(self, explore_index=None) -> None:
        self.explore_index = explore_index

    def check_capability(self, capability: str):
        return {"status": "approved", "capability": capability}

    def check_edit_area(self, path: str):
        return {"status": "approved", "path": path}


class _Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    def read(self, path: str) -> str:
        return (self.root / path).read_text()

    def stat(self, path: str):
        target = self.root / path
        if target.is_dir():
            return {"type": "dir", "size_bytes": 0}
        if target.exists():
            return {"type": "file", "size_bytes": target.stat().st_size}
        return {"type": "missing", "size_bytes": 0}

    def iter_files(self, base: str = ""):
        base_path = self.root / base if base else self.root
        for path in base_path.rglob("*"):
            if path.is_file():
                yield str(path.relative_to(self.root))

    def list_dir(self, base: str):
        target = self.root / base if base else self.root
        return [p.name for p in target.iterdir()]


def _seed_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "hello.py").write_text("def hello():\n    return 'world'\n")
    return workspace


def _decode(result) -> dict:
    return json.loads(result.content[0].text)


def _attach_session(store: ExploreStore, workspace: Path) -> _FakeSession:
    handle = ExploreIndex(
        workspace_root=workspace,
        index_root=store.db_path.parent,
        store=store,
        generation=1,
    )
    return _FakeSession(handle)


def _populate_index(workspace: Path, store: ExploreStore) -> None:
    reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))


def _grep_call(session, workspace, *, use_index: str) -> dict:
    result = handle_grep_files(
        session,
        _Workspace(workspace),
        {
            "pattern": "hello",
            "path": ".",
            "regex": False,
            "case_sensitive": False,
            "use_index": use_index,
        },
    )
    return _decode(result)


def _assert_matches_contain_hello(payload: dict) -> None:
    matches = payload.get("matches", [])
    assert any("hello" in (m.get("text") or "") for m in matches), payload


def test_f1_no_committed_generation_falls_through(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        session = _attach_session(store, workspace)
        payload = _grep_call(session, workspace, use_index="auto")
        assert payload["index_used"] is False
        assert payload["fallback_reason"] == "no_committed_generation"
        _assert_matches_contain_hello(payload)
    finally:
        store.close()


def test_f2_deleted_index_mid_run_falls_through(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        _attach_session(store, workspace)
        db_path = store.db_path
        if db_path.exists():
            db_path.unlink()
        new_store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
        try:
            new_session = _attach_session(new_store, workspace)
            payload = _grep_call(new_session, workspace, use_index="auto")
            assert payload["index_used"] is False
            assert payload["fallback_reason"] == "no_committed_generation"
            _assert_matches_contain_hello(payload)
        finally:
            new_store.close()
    finally:
        store.close()


def test_f3_cold_build_running_falls_through(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        session = _attach_session(store, workspace)
        payload = _grep_call(session, workspace, use_index="auto")
        assert payload["index_used"] is False
        assert payload["fallback_reason"] == "no_committed_generation"
        _assert_matches_contain_hello(payload)
    finally:
        store.close()


def test_f4_version_mismatch_wipes_index(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    index_dir = tmp_path / ".agent" / "ralph-explore"
    store = ExploreStore(index_dir)
    try:
        _populate_index(workspace, store)
        store.set_setting("extractor_version", "stale-extractor-id")
        rebuilt = build_explore_index(workspace)
        assert rebuilt.store is not None
        assert rebuilt.generation == 0
        started = time.monotonic()
        payload = _grep_call(_attach_session(rebuilt.store, workspace), workspace, use_index="auto")
        elapsed = time.monotonic() - started
        _assert_matches_contain_hello(payload)
        assert payload["index_used"] is False
        assert payload["fallback_reason"] in {
            "version_mismatch",
            "no_committed_generation",
        }, payload
        assert elapsed < 1.0, elapsed
    finally:
        store.close()
        new_store = ExploreStore(index_dir)
        try:
            new_session = _attach_session(new_store, workspace)
            _grep_call(new_session, workspace, use_index="auto")
            run_recovery_action(
                build_scheduler(workspace),
                workspace_root=workspace,
                fault_code="version_mismatch",
                reason="schema_drift",
                timeout_ms=10_000,
            )
            rebuilt_store = ExploreStore(index_dir)
            try:
                rebuilt_session = _attach_session(rebuilt_store, workspace)
                served = _grep_call(rebuilt_session, workspace, use_index="auto")
                assert served["index_used"] is True, served
            finally:
                rebuilt_store.close()
        finally:
            new_store.close()


def test_f5_corrupted_index_falls_through(tmp_path: Path) -> None:
    import shutil

    workspace = _seed_workspace(tmp_path)
    index_dir = tmp_path / ".agent" / "ralph-explore"
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "index.sqlite").write_bytes(b"")
    shutil.rmtree(index_dir)
    new_store = ExploreStore(index_dir)
    try:
        new_session = _attach_session(new_store, workspace)
        payload = _grep_call(new_session, workspace, use_index="auto")
        assert payload["index_used"] is False
        _assert_matches_contain_hello(payload)
    finally:
        new_store.close()


def test_f7_locked_database_falls_through_within_budget(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    index_dir = tmp_path / ".agent" / "ralph-explore"
    store = ExploreStore(index_dir, busy_timeout_ms=80)
    _populate_index(workspace, store)
    db_path = store.db_path
    store.close()
    locked_store = ExploreStore(index_dir, busy_timeout_ms=80)
    locker = sqlite3.connect(str(db_path), timeout=0.05)
    locker.execute("BEGIN IMMEDIATE")
    try:
        session = _attach_session(locked_store, workspace)
        started = time.monotonic()
        payload = _grep_call(session, workspace, use_index="auto")
        elapsed = time.monotonic() - started
        assert payload["index_used"] is False
        assert payload["fallback_reason"] == "index_locked"
        _assert_matches_contain_hello(payload)
        assert elapsed < 1.0, elapsed
    finally:
        locked_store.close()
        locker.rollback()
        locker.close()


def test_f11_stale_threshold_falls_through(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        session = _attach_session(store, workspace)
        store.mark_dirty("hello.py", reason="extern_edit", source_tool="test")
        payload = _grep_call(session, workspace, use_index="auto")
        assert payload["index_used"] is False
        assert payload["fallback_reason"] == "index_stale_scope"
        _assert_matches_contain_hello(payload)
    finally:
        store.close()


def test_f12_external_edit_detected_and_falls_through(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        session = _attach_session(store, workspace)
        target = workspace / "hello.py"
        target.write_text("def hello():\n    return 'changed'\n")
        bumped = target.stat().st_mtime_ns + 10_000_000
        import os

        os.utime(target, ns=(bumped, bumped))
        payload = _grep_call(session, workspace, use_index="auto")
        assert payload["fallback_reason"] == "index_stale_scope"
        assert payload["index_used"] is False
        _assert_matches_contain_hello(payload)
    finally:
        store.close()


def test_f13_mass_dirty_paths_falls_through(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    for i in range(20):
        (workspace / f"f{i:02d}.py").write_text(f"def fn_{i}():\n    return {i}\n")
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        session = _attach_session(store, workspace)
        for i in range(20):
            store.mark_dirty(f"f{i:02d}.py", reason="mass", source_tool="test")
        payload = _grep_call(session, workspace, use_index="auto")
        assert payload["fallback_reason"] == "index_stale_scope"
        _assert_matches_contain_hello(payload)
    finally:
        store.close()


def test_f14_ignore_rule_change_triggers_reindex_recovery(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        session = _attach_session(store, workspace)
        (workspace / ".gitignore").write_text("hello.py\n")
        started = time.monotonic()
        payload = _grep_call(session, workspace, use_index="auto")
        elapsed = time.monotonic() - started
        assert payload["index_used"] is False
        assert payload["fallback_reason"] in {
            "ignore_rule_changed",
            "index_stale_scope",
        }, payload
        assert elapsed < 1.0, elapsed
    finally:
        store.close()
    snap = run_recovery_action(
        build_scheduler(workspace),
        workspace_root=workspace,
        fault_code="no_committed_generation",
        reason="ignore_rule_change",
        timeout_ms=10_000,
    )
    assert snap["rebuild_status"] in {"ok", "skipped_no_changes"}, snap
    assert snap["health"] == "healthy", snap


def test_f15_hard_files_skipped_without_crashing(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    (workspace / "binary.dat").write_bytes(b"\x00\x01\x02\x03\x04")
    (workspace / "invalid_utf8.txt").write_bytes(b"hello\xc3\x28world")
    (workspace / "long_line.py").write_text("x = " + "a" * 100_000 + "\n")
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        session = _attach_session(store, workspace)
        payload = _grep_call(session, workspace, use_index="auto")
        assert "matches" in payload
        _assert_matches_contain_hello(payload)
    finally:
        store.close()


def test_f16_regex_falls_through_to_live_grep(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        _populate_index(workspace, store)
        session = _attach_session(store, workspace)
        result = handle_grep_files(
            session,
            _Workspace(workspace),
            {
                "pattern": "h.llo",
                "path": ".",
                "regex": True,
                "use_index": "auto",
            },
        )
        payload = _decode(result)
        assert payload["index_used"] is False
        assert payload["fallback_reason"] == "pattern_not_fts_eligible"
        assert any("hello" in (m.get("text") or "") for m in payload["matches"])
    finally:
        store.close()


def test_f17_timeout_exceeded_returns_incomplete_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(recovery_module, "_ensure_drain_thread", lambda: None)
    workspace = _seed_workspace(tmp_path)
    for i in range(80):
        (workspace / f"extra_{i:02d}.py").write_text(f"def extra_{i}():\n    return {i}\n")
    store = ExploreStore(workspace / ".agent" / "ralph-explore")
    try:
        result = reindex(store, workspace, options=ReindexOptions(timeout_ms=1))
        assert result.status == "timed_out"
        session = _attach_session(store, workspace)
        started = time.monotonic()
        payload = _grep_call(session, workspace, use_index="auto")
        elapsed = time.monotonic() - started
        assert payload["index_used"] is False
        assert payload["fallback_reason"] == "timeout_exceeded"
        _assert_matches_contain_hello(payload)
        assert elapsed < 1.0, elapsed
        run_pending_recovery(workspace, timeout_ms=10_000)
        fresh = ExploreStore(workspace / ".agent" / "ralph-explore")
        try:
            fresh_session = _attach_session(fresh, workspace)
            later = _grep_call(fresh_session, workspace, use_index="auto")
            assert later["index_used"] is True, later
        finally:
            fresh.close()
    finally:
        store.close()


def test_f18_indexer_error_does_not_raise(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    empty_store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    empty_store.close()
    session = _attach_session(empty_store, workspace)
    payload = _grep_call(session, workspace, use_index="auto")
    assert "matches" in payload


def test_f19_resource_pressure_keeps_searches_working(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    session = _FakeSession(explore_index=None)
    started = time.monotonic()
    payload = _grep_call(session, workspace, use_index="auto")
    elapsed = time.monotonic() - started
    _assert_matches_contain_hello(payload)
    assert payload["index_used"] is False
    assert payload["fallback_reason"] in {
        "resource_pressure",
        "no_index_handle",
        "no_committed_generation",
    }, payload
    assert elapsed < 1.0, elapsed
    index_dir = tmp_path / ".agent" / "ralph-explore"
    fresh = ExploreStore(index_dir)
    try:
        _populate_index(workspace, fresh)
        fresh_session = _attach_session(fresh, workspace)
        later = _grep_call(fresh_session, workspace, use_index="auto")
        assert later["index_used"] is True, later
    finally:
        fresh.close()


def test_f20_workspace_moved_falls_through(tmp_path: Path) -> None:
    _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        moved = tmp_path / "moved_ws"
        session = _attach_session(store, moved)
        payload = _grep_call(session, moved, use_index="auto")
        assert payload["index_used"] is False
        assert payload["fallback_reason"] == "no_committed_generation"
    finally:
        store.close()


def test_every_fault_mode_reports_a_canonical_reason_code() -> None:
    used = {
        "no_committed_generation",
        "no_index_handle",
        "index_stale_scope",
        "pattern_not_fts_eligible",
        "index_corrupt",
        "interrupted_build",
        "version_mismatch",
    }
    assert used.issubset(CANONICAL_REASON_CODES)


def test_f9_unwritable_retries_are_bounded_then_recover(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    index_dir = tmp_path / ".agent" / "ralph-explore"
    store = ExploreStore(index_dir)
    try:
        _populate_index(workspace, store)
        session = _attach_session(store, workspace)
        original_mode = index_dir.stat().st_mode
        index_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
        scheduler = build_scheduler(workspace)
        clock_box = [0.0]

        def _clock() -> float:
            return clock_box[0]

        attempts = 0
        try:
            for _ in range(12):
                if not scheduler.should_attempt_recovery(clock=_clock):
                    break
                run_recovery_action(
                    scheduler,
                    workspace_root=workspace,
                    fault_code="index_unwritable",
                    reason="chmod_ro",
                )
                attempts += 1
                clock_box[0] += 100.0
            assert attempts <= scheduler.max_attempts + 1
            assert scheduler.should_attempt_recovery(clock=_clock) is False
            payload = _grep_call(session, workspace, use_index="auto")
            _assert_matches_contain_hello(payload)
        finally:
            index_dir.chmod(original_mode)
        run_recovery_action(
            build_scheduler(workspace),
            workspace_root=workspace,
            fault_code="no_committed_generation",
            reason="writable_again",
            timeout_ms=10_000,
        )
        later = _grep_call(session, workspace, use_index="auto")
        assert later["index_used"] is True, later
    finally:
        store.close()


def test_status_health_during_fault_is_truthful(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        session = _attach_session(store, workspace)
        payload = _grep_call(session, workspace, use_index="auto")
        assert payload["fallback_reason"] == "no_committed_generation"
        status = _decode(handle_ralph_index_status(session, _Workspace(workspace), {}))
        assert status["health"] == "building"
        recovery = status["recovery"]
        assert isinstance(recovery, dict)
        last_failure = recovery["last_failure"]
        assert isinstance(last_failure, dict)
        assert last_failure["code"] == "no_committed_generation"
    finally:
        store.close()


def test_status_health_after_recovery_is_healthy(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        session = _attach_session(store, workspace)
        _grep_call(session, workspace, use_index="auto")
        run_pending_recovery(workspace, timeout_ms=10_000)
        status = _decode(handle_ralph_index_status(session, _Workspace(workspace), {}))
        assert status["health"] == "healthy"
        assert status["recovery"]["last_failure"] is None
    finally:
        store.close()
