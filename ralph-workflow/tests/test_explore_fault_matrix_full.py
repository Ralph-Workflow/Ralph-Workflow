from __future__ import annotations

import contextlib
import json
import shutil
import stat
import time
from pathlib import Path

import pytest

from ralph.mcp.explore.handlers import (
    ExploreIndex,
    build_explore_index,
    handle_ralph_index_status,
)
from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.recovery import (
    HealthState,
    build_scheduler,
    run_recovery_action,
)
from ralph.mcp.explore.serving import CANONICAL_REASON_CODES
from ralph.mcp.explore.store import ExploreStore
from ralph.mcp.tools.workspace._grep_handlers import handle_grep_files

pytestmark = pytest.mark.timeout_seconds(2.0)


class _FakeSession:
    session_id = "fault-full"
    run_id = "fault-full"
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


def test_f1_missing_index_parity_reason_budget_recovery_status() -> None:
    with _TmpWorkspace() as (tmp_path, workspace):
        store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
        try:
            session = _attach_session(store, workspace)
            start = time.monotonic()
            payload = _call_grep(session, workspace)
            elapsed = time.monotonic() - start
            _assert_parity_with_live(payload, workspace)
            assert payload["fallback_reason"] == "no_committed_generation"
            assert payload["fallback_reason"] in CANONICAL_REASON_CODES
            assert elapsed < _budget_seconds(), f"F1 elapsed {elapsed:.3f}s > 1s"
            status = _status_payload(workspace, session)
            assert status["health"] in {"stale", "building", "healthy", "degraded", "unhealthy"}
            _run_recovery(workspace, "no_committed_generation", reason="missing_index")
            new_store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
            try:
                new_session = _attach_session(new_store, workspace)
                later = _call_grep(new_session, workspace)
                assert later["index_used"] is True, (
                    f"after F1 recovery, the call must be served from the index, got {later!r}"
                )
            finally:
                new_store.close()
        finally:
            store.close()


@pytest.mark.timeout_seconds(2.0)
def test_f2_deleted_index_parity_reason_budget_recovery_status() -> None:
    with _TmpWorkspace() as (tmp_path, workspace):
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        seed_store = ExploreStore(index_dir)
        try:
            reindex(seed_store, workspace, options=ReindexOptions(timeout_ms=5_000))
        finally:
            seed_store.close()
        db = index_dir / "index.sqlite"
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(db) + suffix)
            if p.exists():
                p.unlink()
        store = ExploreStore(index_dir)
        try:
            session = _attach_session(store, workspace)
            start = time.monotonic()
            payload = _call_grep(session, workspace)
            elapsed = time.monotonic() - start
            _assert_parity_with_live(payload, workspace)
            assert payload["fallback_reason"] == "no_committed_generation"
            assert elapsed < _budget_seconds(), f"F2 elapsed {elapsed:.3f}s > 1s"
            _run_recovery(workspace, "interrupted_build", reason="deleted_mid_run")
            new_store = ExploreStore(index_dir)
            try:
                new_session = _attach_session(new_store, workspace)
                later = _call_grep(new_session, workspace)
                assert later["index_used"] is True, later
            finally:
                new_store.close()
        finally:
            store.close()


def test_f3_cold_partial_build_parity_reason_budget_recovery_status() -> None:
    with _TmpWorkspace() as (tmp_path, workspace):
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        store = ExploreStore(index_dir)
        try:
            session = _attach_session(store, workspace)
            start = time.monotonic()
            payload = _call_grep(session, workspace)
            elapsed = time.monotonic() - start
            _assert_parity_with_live(payload, workspace)
            assert payload["fallback_reason"] == "no_committed_generation"
            assert elapsed < _budget_seconds(), f"F3 elapsed {elapsed:.3f}s > 1s"
            _run_recovery(workspace, "no_committed_generation", reason="cold_build")
            new_store = ExploreStore(index_dir)
            try:
                new_session = _attach_session(new_store, workspace)
                later = _call_grep(new_session, workspace)
                assert later["index_used"] is True, later
            finally:
                new_store.close()
        finally:
            store.close()


def test_f4_version_mismatch_wipes_and_rebuilds() -> None:
    with _TmpWorkspace() as (tmp_path, workspace):
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        seed_store = ExploreStore(index_dir)
        try:
            reindex(seed_store, workspace, options=ReindexOptions(timeout_ms=5_000))
        finally:
            seed_store.close()
        drift_store = ExploreStore(index_dir)
        try:
            drift_store.set_setting("extractor_version", "stale-extractor-id")
        finally:
            drift_store.close()
        rebuilt = build_explore_index(workspace)
        fault_store = rebuilt.store
        try:
            assert rebuilt.generation == 0
            fault_session = _FakeSession(rebuilt)
            start = time.monotonic()
            fault_payload = _call_grep(fault_session, workspace)
            fault_elapsed = time.monotonic() - start
            _assert_parity_with_live(fault_payload, workspace)
            assert fault_payload["fallback_reason"] in {
                "version_mismatch",
                "no_committed_generation",
            }, fault_payload
            assert fault_payload["fallback_reason"] in CANONICAL_REASON_CODES
            assert fault_elapsed < _budget_seconds(), f"F4 elapsed {fault_elapsed:.3f}s > 1s"
        finally:
            with contextlib.suppress(Exception):
                fault_store.close()
        snap = _run_recovery(workspace, "version_mismatch", reason="schema_drift")
        assert snap is not None
        new_store = ExploreStore(index_dir)
        try:
            new_session = _attach_session(new_store, workspace)
            later = _call_grep(new_session, workspace)
            assert later["index_used"] is True, later
        finally:
            new_store.close()


def test_f5_corrupted_index_quarantines_and_rebuilds() -> None:
    with _TmpWorkspace() as (tmp_path, workspace):
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        seed_store = ExploreStore(index_dir)
        try:
            reindex(seed_store, workspace, options=ReindexOptions(timeout_ms=5_000))
        finally:
            seed_store.close()
        shutil.rmtree(index_dir, ignore_errors=True)
        store = ExploreStore(index_dir)
        try:
            session = _attach_session(store, workspace)
            start = time.monotonic()
            payload = _call_grep(session, workspace)
            elapsed = time.monotonic() - start
            _assert_parity_with_live(payload, workspace)
            assert payload["fallback_reason"] == "no_committed_generation"
            assert elapsed < _budget_seconds(), f"F5 elapsed {elapsed:.3f}s > 1s"
            _run_recovery(workspace, "index_corrupt", reason="missing_dir")
            new_store = ExploreStore(index_dir)
            try:
                new_session = _attach_session(new_store, workspace)
                later = _call_grep(new_session, workspace)
                assert later["index_used"] is True, later
            finally:
                new_store.close()
        finally:
            store.close()


def test_f7_locked_index_falls_through_to_live() -> None:
    from ralph.mcp.explore.recovery import (
        advisory_lock_path,
        release_advisory_lock,
        try_advisory_lock,
    )

    with _TmpWorkspace() as (tmp_path, workspace):
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        store = ExploreStore(index_dir)
        try:
            reindex(store, workspace, options=ReindexOptions(timeout_ms=5_000))
            session = _attach_session(store, workspace)
            lock_path = advisory_lock_path(workspace)
            assert lock_path.parent.is_dir(), f"lock parent missing: {lock_path.parent}"
            first = try_advisory_lock(workspace)
            assert first is True
            second = try_advisory_lock(workspace)
            assert second is False
            try:
                start = time.monotonic()
                payload = _call_grep(session, workspace)
                elapsed = time.monotonic() - start
                _assert_parity_with_live(payload, workspace)
                assert elapsed < _budget_seconds(), f"F7 elapsed {elapsed:.3f}s > 1s"
                snap = _run_recovery(workspace, "index_locked", reason="lock_held")
                assert "health" in snap
            finally:
                release_advisory_lock()
        finally:
            store.close()


def test_f9_unwritable_index_backs_off_and_self_recovers() -> None:
    with _TmpWorkspace() as ctx:
        tmp_path, workspace = ctx
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        store = ExploreStore(index_dir)
        try:
            reindex(store, workspace, options=ReindexOptions(timeout_ms=5_000))
            session = _attach_session(store, workspace)
            original_mode = index_dir.stat().st_mode
            index_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
            try:
                start = time.monotonic()
                payload = _call_grep(session, workspace)
                elapsed = time.monotonic() - start
                _assert_parity_with_live(payload, workspace)
                assert elapsed < _budget_seconds(), f"F9 elapsed {elapsed:.3f}s > 1s"
                snap = _run_recovery(workspace, "index_unwritable", reason="chmod_ro")
                assert snap["recovery_attempts"] >= 1
                index_dir.chmod(original_mode)
                snap2 = _run_recovery(workspace, "no_committed_generation", reason="rebuild")
                assert snap2 is not None
                new_store = ExploreStore(index_dir)
                try:
                    new_session = _attach_session(new_store, workspace)
                    later = _call_grep(new_session, workspace)
                    assert later["index_used"] is True, later
                finally:
                    new_store.close()
            finally:
                with contextlib.suppress(OSError):
                    index_dir.chmod(original_mode)
        finally:
            store.close()


def test_f10_read_only_index_serves_then_recovers_when_writable() -> None:
    with _TmpWorkspace() as (tmp_path, workspace):
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        store = ExploreStore(index_dir)
        try:
            reindex(store, workspace, options=ReindexOptions(timeout_ms=5_000))
            session = _attach_session(store, workspace)
            scheduler = build_scheduler(workspace)
            scheduler.mark_read_only()
            start = time.monotonic()
            _call_grep(session, workspace)
            elapsed = time.monotonic() - start
            assert elapsed < _budget_seconds(), f"F10 elapsed {elapsed:.3f}s > 1s"
            assert scheduler.health is HealthState.DEGRADED
            status = _status_payload(workspace, session)
            assert status["health"] == "degraded"
            snap = _run_recovery(workspace, "no_committed_generation", reason="ro_cleared")
            assert snap["health"] == "healthy"
        finally:
            store.close()


def test_f11_stale_threshold_falls_through_and_recovers() -> None:
    with _TmpWorkspace() as (tmp_path, workspace):
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        store = ExploreStore(index_dir)
        try:
            reindex(store, workspace, options=ReindexOptions(timeout_ms=5_000))
            session = _attach_session(store, workspace)
            store.mark_dirty("hello.py", reason="extern", source_tool="test")
            start = time.monotonic()
            payload = _call_grep(session, workspace)
            elapsed = time.monotonic() - start
            _assert_parity_with_live(payload, workspace)
            assert payload["fallback_reason"] == "index_stale_scope"
            assert elapsed < _budget_seconds(), f"F11 elapsed {elapsed:.3f}s > 1s"
            status = _status_payload(workspace, session)
            assert status["health"] in {"stale", "building", "healthy"}
            _run_recovery(workspace, "no_committed_generation", reason="stale_scope")
            new_store = ExploreStore(index_dir)
            try:
                new_session = _attach_session(new_store, workspace)
                later = _call_grep(new_session, workspace)
                assert later["index_used"] is True, later
            finally:
                new_store.close()
        finally:
            store.close()


def test_f12_external_edit_detected_without_dirty_marking() -> None:
    with _TmpWorkspace() as (tmp_path, workspace):
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        store = ExploreStore(index_dir)
        try:
            reindex(store, workspace, options=ReindexOptions(timeout_ms=5_000))
            session = _attach_session(store, workspace)
            target = workspace / "hello.py"
            target.write_text("def hello():\n    return 'changed_externally'\n")
            import os

            bumped = max(
                target.stat().st_mtime_ns + 10_000_000,
                target.stat().st_mtime_ns + 1,
            )
            os.utime(str(target), ns=(bumped, bumped))
            start = time.monotonic()
            payload = _call_grep(session, workspace)
            elapsed = time.monotonic() - start
            assert payload["fallback_reason"] in {
                "index_stale_scope",
                "no_committed_generation",
            }, payload
            _assert_parity_with_live(payload, workspace)
            assert elapsed < _budget_seconds(), f"F12 elapsed {elapsed:.3f}s > 1s"
            status = _status_payload(workspace, session)
            assert status["health"] in {"stale", "building", "healthy", "degraded"}
        finally:
            store.close()


def test_f13_mass_dirty_paths_falls_through_and_recovers() -> None:
    with _TmpWorkspace() as (tmp_path, workspace):
        for i in range(30):
            (workspace / f"f{i:02d}.py").write_text(f"def fn_{i}():\n    return {i}\n")
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        store = ExploreStore(index_dir)
        try:
            reindex(store, workspace, options=ReindexOptions(timeout_ms=10_000))
            session = _attach_session(store, workspace)
            for i in range(30):
                store.mark_dirty(f"f{i:02d}.py", reason="mass_git", source_tool="test")
            start = time.monotonic()
            payload = _call_grep(session, workspace)
            elapsed = time.monotonic() - start
            _assert_parity_with_live(payload, workspace)
            assert payload["fallback_reason"] == "index_stale_scope"
            assert elapsed < _budget_seconds(), f"F13 elapsed {elapsed:.3f}s > 1s"
            _run_recovery(workspace, "no_committed_generation", reason="mass_git")
            new_store = ExploreStore(index_dir)
            try:
                new_session = _attach_session(new_store, workspace)
                later = _call_grep(new_session, workspace)
                assert later["index_used"] is True, later
            finally:
                new_store.close()
        finally:
            store.close()


def test_f14_ignore_rule_change_rechecks_affected_paths() -> None:
    with _TmpWorkspace() as (tmp_path, workspace):
        index_dir = tmp_path / ".agent" / "ralph-explore"
        index_dir.mkdir(parents=True, exist_ok=True)
        store = ExploreStore(index_dir)
        try:
            reindex(store, workspace, options=ReindexOptions(timeout_ms=5_000))
            session = _attach_session(store, workspace)
            (workspace / ".gitignore").write_text("hello.py\n")
            start = time.monotonic()
            payload = _call_grep(session, workspace, pattern="hello")
            elapsed = time.monotonic() - start
            _assert_parity_with_live(payload, workspace, pattern="hello")
            assert payload["fallback_reason"] in {
                "ignore_rule_changed",
                "index_stale_scope",
            }, payload
            assert payload["fallback_reason"] in CANONICAL_REASON_CODES
            assert elapsed < _budget_seconds(), f"F14 elapsed {elapsed:.3f}s > 1s"
            _run_recovery(workspace, "no_committed_generation", reason="ignore_rule_change")
        finally:
            store.close()


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
