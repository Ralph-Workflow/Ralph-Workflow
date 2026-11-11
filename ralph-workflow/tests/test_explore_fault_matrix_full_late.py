from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path

from ralph.mcp.explore.handlers import (
    ExploreIndex,
    handle_ralph_index_status,
)
from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.recovery import (
    HealthState,
    build_scheduler,
    clear_persisted_state,
    read_persisted_state,
    run_recovery_action,
)
from ralph.mcp.explore.serving import CANONICAL_REASON_CODES
from ralph.mcp.explore.store import ExploreStore
from ralph.mcp.tools.workspace._grep_handlers import handle_grep_files


class _FakeSession:
    session_id = "fault-full-late"
    run_id = "fault-full-late"
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


def _seed_workspace(workspace: Path) -> None:
    (workspace / "hello.py").write_text("def hello():\n    return 'world'\n")
    (workspace / "goodbye.py").write_text("def goodbye():\n    return 'farewell'\n")


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


def _call_grep(session, workspace, *, use_index: str = "auto", pattern: str = "hello") -> dict:
    result = handle_grep_files(
        session,
        _Workspace(workspace),
        {
            "pattern": pattern,
            "path": ".",
            "regex": False,
            "case_sensitive": False,
            "use_index": use_index,
        },
    )
    return _decode(result)


def _live_only_call(workspace: Path, pattern: str = "hello") -> dict:
    session = _FakeSession(explore_index=None)
    return _call_grep(session, workspace, use_index="never", pattern=pattern)


def _matches_set(payload: dict) -> set[tuple[str, int]]:
    return {(str(m.get("path")), int(m.get("line", 0) or 0)) for m in payload.get("matches", [])}


def _status_payload(workspace: Path, session=None) -> dict:
    if session is None:
        session = _FakeSession()
    result = handle_ralph_index_status(session, _Workspace(workspace), {})
    return _decode(result)


def _assert_parity_with_live(payload: dict, workspace: Path, pattern: str = "hello") -> None:
    live = _live_only_call(workspace, pattern=pattern)
    assert _matches_set(payload) == _matches_set(live), (
        f"parity failure: indexed/live diff\n"
        f"  indexed={_matches_set(payload)}\n  live={_matches_set(live)}"
    )


def _budget_seconds() -> float:
    return 1.0


def _run_recovery(workspace: Path, fault_code: str, reason: str = "") -> dict:
    scheduler = build_scheduler(workspace)
    return run_recovery_action(
        scheduler,
        workspace_root=workspace,
        fault_code=fault_code,
        reason=reason,
    )


class _TmpWorkspace:
    def __enter__(self) -> tuple[Path, Path]:
        import tempfile

        self._tmp_ctx = tempfile.TemporaryDirectory()
        tmp_path = Path(self._tmp_ctx.name)
        workspace = tmp_path
        _seed_workspace(workspace)
        return tmp_path, workspace

    def __exit__(self, *exc_info: object) -> None:
        self._tmp_ctx.cleanup()


def test_f15_hard_files_skip_without_crashing() -> None:
    with _TmpWorkspace() as (tmp_path, workspace):
        (workspace / "binary.dat").write_bytes(b"\x00\x01\x02\x03")
        (workspace / "invalid_utf8.txt").write_bytes(b"hello\xc3\x28world")
        (workspace / "long_line.py").write_text("x = " + "a" * 100_000 + "\n")
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        store = ExploreStore(index_dir)
        try:
            start = time.monotonic()
            reindex(store, workspace, options=ReindexOptions(timeout_ms=10_000))
            elapsed = time.monotonic() - start
            assert elapsed < 5.0, f"F15 cold build {elapsed:.3f}s > 5s"
            row = store.get_file("hello.py")
            assert row is not None
            session = _attach_session(store, workspace)
            start = time.monotonic()
            payload = _call_grep(session, workspace)
            elapsed = time.monotonic() - start
            _assert_parity_with_live(payload, workspace)
            paths = {m.get("path") for m in payload["matches"]}
            assert "hello.py" in paths, paths
            assert "binary.dat" not in paths, paths
            assert "invalid_utf8.txt" not in paths, paths
            assert "long_line.py" not in paths, paths
            assert elapsed < _budget_seconds(), f"F15 elapsed {elapsed:.3f}s > {_budget_seconds()}s"
            assert payload.get("index_used") is True, payload
            assert payload.get("fallback_reason") is None, payload
            status_during = _status_payload(workspace, session)
            assert status_during["health"] in {
                "healthy",
                "building",
            }, status_during
            later = _call_grep(session, workspace)
            assert later["index_used"] is True, later
            status_after = _status_payload(workspace, session)
            assert status_after["health"] == "healthy", status_after
        finally:
            store.close()


def test_f16_regex_falls_through_to_live() -> None:
    with _TmpWorkspace() as (tmp_path, workspace):
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        store = ExploreStore(index_dir)
        try:
            reindex(store, workspace, options=ReindexOptions(timeout_ms=5_000))
            session = _attach_session(store, workspace)
            start = time.monotonic()
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
            elapsed = time.monotonic() - start
            payload = _decode(result)
            live_result = handle_grep_files(
                session,
                _Workspace(workspace),
                {
                    "pattern": "h.llo",
                    "path": ".",
                    "regex": True,
                    "use_index": "never",
                },
            )
            live_payload = _decode(live_result)
            assert _matches_set(payload) == _matches_set(live_payload), (
                f"parity failure: indexed/live diff\n"
                f"  indexed={_matches_set(payload)}\n"
                f"  live={_matches_set(live_payload)}"
            )
            assert payload["index_used"] is False
            assert payload["fallback_reason"] == "pattern_not_fts_eligible"
            assert any("hello" in (m.get("text") or "") for m in payload["matches"])
            assert elapsed < _budget_seconds(), f"F16 elapsed {elapsed:.3f}s > {_budget_seconds()}s"
            status_during = _status_payload(workspace, session)
            assert status_during["health"] == "healthy", status_during
            later = _call_grep(session, workspace, pattern="hello")
            assert later["index_used"] is True, later
            assert later["fallback_reason"] is None, later
        finally:
            store.close()


def test_f17_timeout_returns_bounded_partial() -> None:
    with _TmpWorkspace() as (tmp_path, workspace):
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        store = ExploreStore(index_dir)
        try:
            start = time.monotonic()
            with contextlib.suppress(Exception):
                reindex(store, workspace, options=ReindexOptions(timeout_ms=1))
            elapsed = time.monotonic() - start
            assert elapsed < 2.0, f"F17 elapsed {elapsed:.3f}s > 2s"
            session = _attach_session(store, workspace)
            payload = _call_grep(session, workspace)
            _assert_parity_with_live(payload, workspace)
        finally:
            store.close()


def test_f18_indexer_exception_falls_through() -> None:
    with _TmpWorkspace() as (tmp_path, workspace):
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        empty_store = ExploreStore(index_dir)
        empty_store.close()
        session = _attach_session(empty_store, workspace)
        start = time.monotonic()
        payload = _call_grep(session, workspace)
        elapsed = time.monotonic() - start
        _assert_parity_with_live(payload, workspace)
        assert elapsed < _budget_seconds(), f"F18 elapsed {elapsed:.3f}s > 1s"
        snap = _run_recovery(workspace, "indexer_error", reason="closed_conn")
        assert "health" in snap


def test_f19_resource_pressure_keeps_searches_working() -> None:
    with _TmpWorkspace() as (tmp_path, workspace):
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        store = ExploreStore(index_dir)
        try:
            session = _FakeSession(explore_index=None)
            start = time.monotonic()
            payload = _call_grep(session, workspace)
            elapsed = time.monotonic() - start
            _assert_parity_with_live(payload, workspace)
            assert payload["fallback_reason"] in {
                "resource_pressure",
                "no_index_handle",
                "no_committed_generation",
            }, payload
            assert payload["fallback_reason"] in CANONICAL_REASON_CODES
            assert elapsed < _budget_seconds(), f"F19 elapsed {elapsed:.3f}s > 1s"
            scheduler = build_scheduler(workspace)
            scheduler.observe(
                code="resource_pressure",
                message="low_memory_simulation",
                health=HealthState.UNHEALTHY,
            )
            populated_handle = ExploreIndex(
                workspace_root=workspace,
                index_root=index_dir,
                store=store,
                generation=1,
            )
            status_session = _FakeSession(populated_handle)
            status = _status_payload(workspace, status_session)
            assert status["recovery"]["last_failure"]["code"] == "resource_pressure"
            reindex(store, workspace, options=ReindexOptions(timeout_ms=5_000))
            scheduler.mark_healthy()
            new_session = _attach_session(store, workspace)
            later = _call_grep(new_session, workspace)
            assert later["index_used"] is True, later
        finally:
            store.close()


def test_f20_workspace_moved_falls_through_and_recovers() -> None:
    with _TmpWorkspace() as (tmp_path, workspace):
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        store = ExploreStore(index_dir)
        try:
            reindex(store, workspace, options=ReindexOptions(timeout_ms=5_000))
        finally:
            store.close()
        moved = tmp_path / "moved_ws"
        moved.mkdir()
        (moved / "hello.py").write_text("def hello():\n    return 'world'\n")
        moved_index_dir = moved / ".agent" / "ralph-explore"
        moved_index_dir.mkdir(parents=True, exist_ok=True)
        moved_store = ExploreStore(moved_index_dir)
        try:
            session = _attach_session(moved_store, moved)
            start = time.monotonic()
            payload = _call_grep(session, moved)
            elapsed = time.monotonic() - start
            _assert_parity_with_live(payload, moved)
            assert payload["fallback_reason"] in {
                "no_committed_generation",
                "workspace_moved",
            }, payload
            assert elapsed < _budget_seconds(), f"F20 elapsed {elapsed:.3f}s > 1s"
            _run_recovery(moved, "workspace_moved", reason="path_change")
            later_store = ExploreStore(moved_index_dir)
            try:
                later_session = _attach_session(later_store, moved)
                later = _call_grep(later_session, moved)
                assert later["index_used"] is True, later
            finally:
                later_store.close()
        finally:
            moved_store.close()


def test_status_payload_truthful_for_every_fault_mode() -> None:
    with _TmpWorkspace() as (_tmp_path, workspace):
        for fault_code, expected_health in [
            ("index_corrupt", "stale"),
            ("interrupted_build", "stale"),
            ("version_mismatch", "stale"),
            ("index_unwritable", "stale"),
            ("index_locked", "stale"),
            ("indexer_error", "stale"),
            ("workspace_moved", "stale"),
        ]:
            clear_persisted_state(workspace)
            scheduler = build_scheduler(workspace)
            scheduler.record_failure(code=fault_code, message=f"F_{fault_code}_test")
            persisted = read_persisted_state(workspace)
            assert persisted is not None, f"no persisted state for {fault_code}"
            assert persisted["last_failure_code"] == fault_code
            assert persisted["health"] == expected_health
            assert scheduler.snapshot()["last_failure"]["code"] == fault_code
            assert scheduler.snapshot()["health"] == expected_health
