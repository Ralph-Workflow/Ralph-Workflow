from __future__ import annotations

import dataclasses
import sqlite3
import threading
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

from ralph.mcp.explore.pipeline import (
    DEFAULT_TIMEOUT_MS,
    ReindexOptions,
    ReindexResult,
    ReindexWriter,
    reindex,
)
from ralph.mcp.explore.store import (
    ContentCacheRow,
    ExploreStore,
    FileRow,
)

pytestmark = pytest.mark.timeout_seconds(5)


class FakeClock:
    def __init__(self, initial: float = 1_000.0) -> None:
        self._t = initial

    def now(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


class _SequenceClock:
    def __init__(self, values: list[float]) -> None:
        self._values = list(values)
        self._idx = 0
        self._t: float = self._values[0] if self._values else 0.0

    def now(self) -> float:
        if self._idx >= len(self._values):
            value = self._values[-1]
        else:
            value = self._values[self._idx]
            self._idx += 1
        self._t = value
        return value


def _seed_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.py").write_text("def hello():\n    return 1\n")
    (workspace / "b.py").write_text("x = 1\n" * 50)
    (workspace / "README.md").write_text("# Title\n\nSome body text.\n")
    return workspace


def _count_files_rows(store: ExploreStore) -> int:
    cur = sqlite3.connect(str(store.db_path))
    try:
        return int(cur.execute("SELECT COUNT(*) FROM files").fetchone()[0])
    finally:
        cur.close()


def _count_chunks_rows(store: ExploreStore) -> int:
    cur = sqlite3.connect(str(store.db_path))
    try:
        return int(cur.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
    finally:
        cur.close()


def _count_fts_rows(store: ExploreStore) -> int:
    cur = sqlite3.connect(str(store.db_path))
    try:
        return int(cur.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0])
    finally:
        cur.close()


def _build_store(tmp_path: Path) -> ExploreStore:
    return ExploreStore(tmp_path / ".agent" / "ralph-explore")


def test_cold_build_creates_manifest_and_generation(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        result = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert result.status == "ok"
        assert store.get_setting("current_generation") == "1"
        assert _count_files_rows(store) >= 3
        assert _count_chunks_rows(store) >= 3
        assert _count_fts_rows(store) >= 3
    finally:
        store.close()


def test_warm_no_op_reindex_parses_zero_files(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        first = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        second = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert first.parse_count >= 1
        assert second.parse_count == 0
        assert second.status == "skipped_no_changes"
    finally:
        store.close()


def test_warm_no_op_does_not_duplicate_rows(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        files_after_first = _count_files_rows(store)
        chunks_after_first = _count_chunks_rows(store)
        fts_after_first = _count_fts_rows(store)
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert _count_files_rows(store) == files_after_first
        assert _count_chunks_rows(store) == chunks_after_first
        assert _count_fts_rows(store) == fts_after_first
    finally:
        store.close()


def test_warm_small_edit_reparses_only_changed_file(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        (workspace / "a.py").write_text("def hello():\n    return 42\n")
        result = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert result.status == "ok"
        assert result.parse_count == 1
        assert tuple(result.changed_files) == ("a.py",)
    finally:
        store.close()


def test_delete_path_marks_file_deleted(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        (workspace / "b.py").unlink()
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert store.get_file("b.py") is None
        assert store.count_deleted_files() == 0
        tombstones = store._conn.execute(
            """
            SELECT stale_reason FROM evidence_tombstones
            WHERE path = ? AND stale_reason = 'file_deleted'
            """,
            ("b.py",),
        ).fetchall()
        assert len(tombstones) == 1
    finally:
        store.close()


def test_partial_failure_records_failed_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        from ralph.mcp.explore import pipeline

        original = pipeline.hash_workspace_file

        def flaky(workspace_root: Path, relative_path: str):
            if relative_path == "a.py":
                raise OSError("simulated failure")
            return original(workspace_root, relative_path)

        monkeypatch.setattr("ralph.mcp.explore.pipeline.hash_workspace_file", flaky)
        result = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert "a.py" in result.failed_files
        b_row = store.get_file("b.py")
        assert b_row is not None and b_row.is_deleted is False
    finally:
        store.close()


def test_mode_full_rebuilds_atomically(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        (workspace / "a.py").write_text('NEW = "CONTENT"\n')
        result = reindex(
            store,
            workspace,
            options=ReindexOptions(mode="full", timeout_ms=DEFAULT_TIMEOUT_MS),
        )
        assert result.status == "ok"
        row = store.get_file("a.py")
        assert row is not None
        assert store.get_setting("current_generation") == "1"
    finally:
        store.close()


def test_mode_full_persists_wall_clock_job_timestamps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    wall_now = 1_800_000_000.0
    monkeypatch.setattr(
        "ralph.mcp.explore._pipeline_staged.real_clock_seconds",
        lambda: wall_now,
    )
    try:
        result = reindex(
            store,
            workspace,
            options=ReindexOptions(
                mode="full",
                timeout_ms=DEFAULT_TIMEOUT_MS,
                clock=FakeClock(initial=2_000.0),
            ),
        )

        assert result.status == "ok"
        latest = store.latest_job()
        assert latest is not None
        assert latest["finished_at"] == wall_now
        assert result.elapsed_seconds == 0.0
    finally:
        store.close()


def test_mode_full_pre_cancel_preserves_committed_generation(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        first = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert first.status == "ok", first.error_summary
        prior_generation = int(store.get_setting("current_generation") or 0)
        prior_generation_str = store.get_setting("current_generation")
        assert prior_generation >= 1
        files_before = _count_files_rows(store)
        chunks_before = _count_chunks_rows(store)
        fts_before = _count_fts_rows(store)
        assert files_before >= 3

        def _cancelled() -> bool:
            return True

        result = reindex(
            store,
            workspace,
            options=ReindexOptions(mode="full", timeout_ms=DEFAULT_TIMEOUT_MS),
            cancel=_cancelled,
        )
        assert result.status == "cancelled"

        assert store.get_setting("current_generation") == prior_generation_str
        assert _count_files_rows(store) == files_before
        assert _count_chunks_rows(store) == chunks_before
        assert _count_fts_rows(store) == fts_before

        recover = reindex(
            store,
            workspace,
            options=ReindexOptions(mode="full", timeout_ms=DEFAULT_TIMEOUT_MS),
        )
        assert recover.status == "ok", recover.error_summary
        assert _count_files_rows(store) == files_before
        assert _count_chunks_rows(store) == chunks_before
        assert _count_fts_rows(store) == fts_before
    finally:
        store.close()


def test_mode_full_mid_build_cancel_preserves_committed_generation(
    tmp_path: Path,
) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        first = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert first.status == "ok", first.error_summary
        prior_generation_str = store.get_setting("current_generation")
        assert prior_generation_str is not None
        files_before = _count_files_rows(store)
        chunks_before = _count_chunks_rows(store)
        fts_before = _count_fts_rows(store)
        assert files_before >= 3

        call_state = {"n": 0}

        def _mid_cancel() -> bool:
            call_state["n"] += 1
            return call_state["n"] > 1

        result = reindex(
            store,
            workspace,
            options=ReindexOptions(mode="full", timeout_ms=DEFAULT_TIMEOUT_MS),
            cancel=_mid_cancel,
        )
        assert result.status == "cancelled"

        assert store.get_setting("current_generation") == prior_generation_str
        assert _count_files_rows(store) == files_before
        assert _count_chunks_rows(store) == chunks_before
        assert _count_fts_rows(store) == fts_before

        staging_root = store.index_dir
        leftovers = [
            child for child in staging_root.iterdir() if child.name.startswith(".staging-full-")
        ]
        assert leftovers == []

        recover = reindex(
            store,
            workspace,
            options=ReindexOptions(mode="full", timeout_ms=DEFAULT_TIMEOUT_MS),
        )
        assert recover.status == "ok", recover.error_summary
        assert _count_files_rows(store) == files_before
        assert _count_chunks_rows(store) == chunks_before
        assert _count_fts_rows(store) == fts_before
    finally:
        store.close()


def test_mode_full_mid_build_timeout_preserves_committed_generation(
    tmp_path: Path,
) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        first = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert first.status == "ok", first.error_summary
        prior_generation_str = store.get_setting("current_generation")
        assert prior_generation_str is not None
        files_before = _count_files_rows(store)
        chunks_before = _count_chunks_rows(store)
        fts_before = _count_fts_rows(store)

        clock = _SequenceClock([1_000.0, 1_000.0, 1_010.0, 1_010.0, 1_010.0])
        result = reindex(
            store,
            workspace,
            options=ReindexOptions(
                mode="full",
                timeout_ms=DEFAULT_TIMEOUT_MS,
                clock=clock,
            ),
        )
        assert result.status == "timed_out", result.error_summary
        assert store.get_setting("current_generation") == prior_generation_str
        assert _count_files_rows(store) == files_before
        assert _count_chunks_rows(store) == chunks_before
        assert _count_fts_rows(store) == fts_before
        leftovers = [
            child for child in store.index_dir.iterdir() if child.name.startswith(".staging-full-")
        ]
        assert leftovers == []
    finally:
        store.close()


def test_mode_full_swap_io_failure_preserves_committed_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        first = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert first.status == "ok"
        prior_generation_str = store.get_setting("current_generation")
        assert prior_generation_str is not None
        files_before = _count_files_rows(store)
        chunks_before = _count_chunks_rows(store)
        fts_before = _count_fts_rows(store)
        assert files_before >= 3

        swap_failures = {"n": 0}

        def fail_atomic_publication(*args: object, **kwargs: object) -> bool:
            del args, kwargs
            swap_failures["n"] += 1
            raise OSError("simulated disk full during swap")

        monkeypatch.setattr(
            "ralph.mcp.explore._pipeline_staged.atomic_write_bytes_if_changed",
            fail_atomic_publication,
        )

        result = reindex(
            store,
            workspace,
            options=ReindexOptions(mode="full", timeout_ms=DEFAULT_TIMEOUT_MS),
        )
        assert result.status == "failed"
        assert swap_failures["n"] >= 1

        assert store.get_setting("current_generation") == prior_generation_str
        assert _count_files_rows(store) == files_before
        assert _count_chunks_rows(store) == chunks_before
        assert _count_fts_rows(store) == fts_before

        a_row = store.get_file("a.py")
        assert a_row is not None
        assert a_row.is_deleted is False
        assert a_row.content_hash is not None

        swap_dir = store.db_path.parent
        leftovers = [child for child in swap_dir.iterdir() if child.name.endswith(".swap")]
        assert leftovers == []

        monkeypatch.undo()
        recover = reindex(
            store,
            workspace,
            options=ReindexOptions(mode="full", timeout_ms=DEFAULT_TIMEOUT_MS),
        )
        assert recover.status == "ok"
        assert _count_files_rows(store) == files_before
        assert _count_chunks_rows(store) == chunks_before
        assert _count_fts_rows(store) == fts_before
    finally:
        store.close()


@pytest.mark.timeout_seconds(2.0)
def test_mode_full_swap_refused_when_outer_deadline_already_exceeded(
    tmp_path: Path,
) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        first = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert first.status == "ok"
        prior_generation_str = store.get_setting("current_generation")
        assert prior_generation_str is not None
        files_before = _count_files_rows(store)
        chunks_before = _count_chunks_rows(store)
        fts_before = _count_fts_rows(store)
        assert files_before >= 3

        clock = _SequenceClock([1_000.0, 1_000.0, 1_010.0, 1_010.0, 1_010.0])
        result = reindex(
            store,
            workspace,
            options=ReindexOptions(
                mode="full",
                timeout_ms=DEFAULT_TIMEOUT_MS,
                clock=clock,
            ),
        )
        assert result.status == "timed_out"
        assert store.get_setting("current_generation") == prior_generation_str
        assert _count_files_rows(store) == files_before
        assert _count_chunks_rows(store) == chunks_before
        assert _count_fts_rows(store) == fts_before
        leftovers = [
            child for child in store.index_dir.iterdir() if child.name.startswith(".staging-full-")
        ]
        assert leftovers == []
        recover = reindex(
            store,
            workspace,
            options=ReindexOptions(mode="full", timeout_ms=DEFAULT_TIMEOUT_MS),
        )
        assert recover.status == "ok"
        assert _count_files_rows(store) == files_before
        assert _count_chunks_rows(store) == chunks_before
        assert _count_fts_rows(store) == fts_before
    finally:
        store.close()


@pytest.mark.timeout_seconds(2.0)
def test_mode_full_swap_refused_when_outer_cancellation_already_requested(
    tmp_path: Path,
) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        first = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert first.status == "ok"
        prior_generation_str = store.get_setting("current_generation")
        assert prior_generation_str is not None
        files_before = _count_files_rows(store)
        chunks_before = _count_chunks_rows(store)
        fts_before = _count_fts_rows(store)

        cancel_state = {"n": 0}

        def _cancel_at_swap() -> bool:
            cancel_state["n"] += 1
            return cancel_state["n"] >= 4

        result = reindex(
            store,
            workspace,
            options=ReindexOptions(
                mode="full",
                timeout_ms=DEFAULT_TIMEOUT_MS,
            ),
            cancel=_cancel_at_swap,
        )
        assert result.status == "cancelled"
        assert store.get_setting("current_generation") == prior_generation_str
        assert _count_files_rows(store) == files_before
        assert _count_chunks_rows(store) == chunks_before
        assert _count_fts_rows(store) == fts_before
        leftovers = [
            child for child in store.index_dir.iterdir() if child.name.startswith(".staging-full-")
        ]
        assert leftovers == []
        recover = reindex(
            store,
            workspace,
            options=ReindexOptions(mode="full", timeout_ms=DEFAULT_TIMEOUT_MS),
        )
        assert recover.status == "ok"
        assert _count_files_rows(store) == files_before
        assert _count_chunks_rows(store) == chunks_before
        assert _count_fts_rows(store) == fts_before
    finally:
        store.close()


@pytest.mark.timeout_seconds(2.0)
def test_timeout_is_fail_closed_for_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        clock = FakeClock(initial=1_000.0)
        from ralph.mcp.explore import pipeline

        original_hash = pipeline.hash_workspace_file
        call_count = {"n": 0}

        def slow_hash(workspace_root: Path, relative_path: str):
            call_count["n"] += 1
            if call_count["n"] == 1:
                clock.advance(0.1)
            return original_hash(workspace_root, relative_path)

        monkeypatch.setattr("ralph.mcp.explore.pipeline.hash_workspace_file", slow_hash)
        result = reindex(
            store,
            workspace,
            options=ReindexOptions(timeout_ms=1, clock=clock),
        )
        assert result.status == "timed_out"
    finally:
        store.close()


def test_consume_dirty_paths_during_reindex(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        store.mark_dirty("a.py", reason="write", source_tool="write_file")
        assert store.peek_dirty_paths() == ["a.py"]
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert store.peek_dirty_paths() == []
    finally:
        store.close()


def test_tombstone_created_on_content_change(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        (workspace / "a.py").write_text("def hello():\n    return 99\n")
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        cur = sqlite3.connect(str(store.db_path))
        try:
            count = cur.execute(
                "SELECT COUNT(*) FROM evidence_tombstones WHERE path = ?",
                ("a.py",),
            ).fetchone()[0]
        finally:
            cur.close()
        assert count >= 1
    finally:
        store.close()


def test_expired_tombstone_returns_retention_expired(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        (workspace / "a.py").write_text("def hello():\n    return 99\n")
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        cur = sqlite3.connect(str(store.db_path))
        try:
            cur.execute(
                "UPDATE evidence_tombstones SET stale_at = ?",
                (time.time() - 31 * 24 * 60 * 60,),
            )
            cur.commit()
        finally:
            cur.close()
        store.record_tombstone(
            evidence_id="ev-fresh",
            path="b.py",
            start_line=1,
            end_line=2,
            content_hash="x",
            generation=2,
            stale_reason="manual",
            stale_at=time.time(),
            replacement_evidence_id=None,
        )
        cur = sqlite3.connect(str(store.db_path))
        try:
            count = cur.execute(
                "SELECT COUNT(*) FROM evidence_tombstones WHERE path = ?",
                ("a.py",),
            ).fetchone()[0]
        finally:
            cur.close()
        assert count == 0
    finally:
        store.close()


def test_path_scope_limits_work(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        result = reindex(
            store,
            workspace,
            options=ReindexOptions(
                mode="full", timeout_ms=DEFAULT_TIMEOUT_MS, path_scope=("a.py",)
            ),
        )
        assert result.status == "ok"
        assert "a.py" in result.changed_files
        assert "b.py" not in result.changed_files
    finally:
        store.close()


def test_reindex_writer_coalesces(tmp_path: Path) -> None:
    ReindexWriter.configure(lock_factory=threading.Lock)
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        lock_key = str(store.db_path)
        ReindexWriter._active[lock_key] = ReindexWriter(store)
        try:
            result = ReindexWriter.claim(store, workspace_root=workspace)
            assert result.status == "skipped_no_changes"
        finally:
            ReindexWriter._active.pop(lock_key, None)
    finally:
        store.close()


def test_reindex_writer_returns_result_when_uncontended(tmp_path: Path) -> None:
    ReindexWriter.configure(lock_factory=threading.Lock)
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        result = ReindexWriter.claim(store, workspace_root=workspace)
        assert result.status in {"ok", "skipped_no_changes"}
    finally:
        store.close()


def test_coalesces_handler_and_lifecycle_claims_while_writer_is_active(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from ralph.mcp.explore import pipeline as pipeline_module
    from ralph.mcp.explore.lifecycle import claim_reindex

    class _Store:
        db_path = tmp_path / "index.sqlite"

        @staticmethod
        def get_setting(_key: str) -> str:
            return "7"

        @staticmethod
        def peek_dirty_paths() -> tuple[str, ...]:
            return ("changed.py",)

    ReindexWriter.configure(lock_factory=threading.Lock)
    store = _Store()
    owner_entered = threading.Event()
    release_owner = threading.Event()
    owner_results: list[ReindexResult] = []
    reindex_calls: list[ReindexOptions | None] = []

    def _fake_reindex(
        _store: object,
        _workspace_root: Path,
        *,
        options: ReindexOptions | None = None,
        cancel: object = None,
    ) -> ReindexResult:
        reindex_calls.append(options)
        owner_entered.set()
        assert release_owner.wait(timeout=1), "test must release the active writer"
        return ReindexResult(job_id="owner", generation=8, status="ok")

    monkeypatch.setattr(pipeline_module, "reindex", _fake_reindex)

    def _owner_claim() -> None:
        owner_results.append(ReindexWriter.claim(store, workspace_root=tmp_path))

    owner = threading.Thread(target=_owner_claim)
    owner.start()
    assert owner_entered.wait(timeout=1), "owner must reach the reindex boundary"

    lifecycle_result = claim_reindex(
        store,
        tmp_path,
        options=ReindexOptions(mode="changed", timeout_ms=5000),
    )
    release_owner.set()
    owner.join(timeout=1)

    assert not owner.is_alive(), "owner must finish after the test releases it"
    assert reindex_calls == [None]
    assert owner_results == [ReindexResult(job_id="owner", generation=8, status="ok")]
    assert lifecycle_result == ReindexResult(
        job_id="coalesced-index.sqlite",
        generation=7,
        status="skipped_no_changes",
        dirty_paths_count=1,
    )


def test_reindex_records_job_history(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        cur = sqlite3.connect(str(store.db_path))
        try:
            count = cur.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        finally:
            cur.close()
        assert count >= 1
    finally:
        store.close()


def test_changed_reindex_clears_structure_rows_for_deleted_path(
    tmp_path: Path,
) -> None:
    workspace = _seed_workspace(tmp_path)
    (workspace / "gone.py").write_text("def gone():\n    return 1\n")
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert [s.qualified_name for s in store.iter_symbols("gone.py")] == ["gone.gone"]
        (workspace / "gone.py").unlink()
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))

        assert list(store.iter_symbols("gone.py")) == []
        assert list(store.iter_spans("gone.py")) == []
        assert list(store.iter_edges(path="gone.py")) == []
        cur = sqlite3.connect(str(store.db_path))
        try:
            chunk_count = cur.execute(
                "SELECT COUNT(*) FROM chunks WHERE path = ?", ("gone.py",)
            ).fetchone()[0]
            fts_count = cur.execute(
                "SELECT COUNT(*) FROM chunks_fts WHERE path = ?", ("gone.py",)
            ).fetchone()[0]
            evidence_count = cur.execute(
                "SELECT COUNT(*) FROM evidence WHERE path = ?", ("gone.py",)
            ).fetchone()[0]
        finally:
            cur.close()
        assert chunk_count == 0
        assert fts_count == 0
        assert evidence_count == 0
    finally:
        store.close()


def test_full_reindex_clears_structure_rows_for_deleted_path(
    tmp_path: Path,
) -> None:
    workspace = _seed_workspace(tmp_path)
    (workspace / "gone.py").write_text("def gone():\n    return 1\n")
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert [s.qualified_name for s in store.iter_symbols("gone.py")] == ["gone.gone"]
        (workspace / "gone.py").unlink()
        reindex(
            store,
            workspace,
            options=ReindexOptions(mode="full", timeout_ms=DEFAULT_TIMEOUT_MS),
        )
        cur = sqlite3.connect(str(store.db_path))
        try:
            span_count = cur.execute(
                "SELECT COUNT(*) FROM spans WHERE path = ?", ("gone.py",)
            ).fetchone()[0]
            symbol_count = cur.execute(
                "SELECT COUNT(*) FROM symbols WHERE path = ?", ("gone.py",)
            ).fetchone()[0]
            edge_count = cur.execute(
                "SELECT COUNT(*) FROM edges WHERE path = ?", ("gone.py",)
            ).fetchone()[0]
            chunk_count = cur.execute(
                "SELECT COUNT(*) FROM chunks WHERE path = ?", ("gone.py",)
            ).fetchone()[0]
            fts_count = cur.execute(
                "SELECT COUNT(*) FROM chunks_fts WHERE path = ?", ("gone.py",)
            ).fetchone()[0]
            evidence_count = cur.execute(
                "SELECT COUNT(*) FROM evidence WHERE path = ?", ("gone.py",)
            ).fetchone()[0]
        finally:
            cur.close()
        assert span_count == 0
        assert symbol_count == 0
        assert edge_count == 0
        assert chunk_count == 0
        assert fts_count == 0
        assert evidence_count == 0
    finally:
        store.close()


def test_delete_then_identical_restore_reindexes_path(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    (workspace / "restore.py").write_text("def original():\n    return 1\n")
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert [s.qualified_name for s in store.iter_symbols("restore.py")] == ["restore.original"]
        (workspace / "restore.py").unlink()
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        deleted_row = store.get_file("restore.py")
        assert deleted_row is None
        (workspace / "restore.py").write_text("def original():\n    return 1\n")
        result = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert result.status == "ok"
        assert "restore.py" in tuple(result.changed_files)
        restored_row = store.get_file("restore.py")
        assert restored_row is not None
        assert restored_row.is_deleted is False
        cur = sqlite3.connect(str(store.db_path))
        try:
            chunk_count = cur.execute(
                "SELECT COUNT(*) FROM chunks WHERE path = ?", ("restore.py",)
            ).fetchone()[0]
            fts_count = cur.execute(
                "SELECT COUNT(*) FROM chunks_fts WHERE path = ?", ("restore.py",)
            ).fetchone()[0]
            evidence_count = cur.execute(
                "SELECT COUNT(*) FROM evidence WHERE path = ?", ("restore.py",)
            ).fetchone()[0]
            symbol_count = cur.execute(
                "SELECT COUNT(*) FROM symbols WHERE path = ?", ("restore.py",)
            ).fetchone()[0]
        finally:
            cur.close()
        assert chunk_count > 0
        assert fts_count > 0
        assert evidence_count > 0
        assert symbol_count > 0
        assert [s.qualified_name for s in store.iter_symbols("restore.py")] == ["restore.original"]
    finally:
        store.close()


def test_malformed_changed_python_preserves_prior_rows_and_retries(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    (workspace / "a.py").write_text("def hello():\n    return 1\n\ndef goodbye():\n    return 2\n")
    store = _build_store(tmp_path)
    try:
        reindex(
            store,
            workspace,
            options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS),
        )
        before_chunks = _count_chunks_rows(store)
        before_symbols = len(list(store.iter_symbols("a.py")))
        assert before_chunks > 0
        assert before_symbols > 0
        before_qual = {sym.qualified_name for sym in store.iter_symbols("a.py")}
        assert "a.hello" in before_qual

        (workspace / "a.py").write_text("def broken(:\n    pass\n")
        store.mark_dirty("a.py", reason="write", source_tool="write_file")
        assert "a.py" in store.peek_dirty_paths()

        result = reindex(
            store,
            workspace,
            options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS),
        )
        assert "a.py" in result.failed_files
        assert "a.py" not in result.changed_files

        assert _count_chunks_rows(store) == before_chunks
        qual_after = {sym.qualified_name for sym in store.iter_symbols("a.py")}
        assert qual_after == before_qual

        assert "a.py" in store.peek_dirty_paths()

        other_row = store.get_file("b.py")
        assert other_row is not None
        assert other_row.is_deleted is False

        (workspace / "a.py").write_text("def hello():\n    return 42\n")
        result2 = reindex(
            store,
            workspace,
            options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS),
        )
        assert result2.status == "ok"
        assert "a.py" in result2.changed_files
        assert "a.py" not in store.peek_dirty_paths()
        qual_recovered = {sym.qualified_name for sym in store.iter_symbols("a.py")}
        assert "a.hello" in qual_recovered
    finally:
        store.close()


@pytest.mark.timeout_seconds(2.0)
def test_tombstone_record_is_idempotent_on_repeat_delete(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    (workspace / "t.py").write_text("def t():\n    return 1\n")
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        (workspace / "t.py").unlink()
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        (workspace / "t.py").write_text("def t():\n    return 2\n")
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        (workspace / "t.py").unlink()
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        (workspace / "t.py").write_text("def t():\n    return 1\n")
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        (workspace / "t.py").unlink()
        result = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert result.status in {"ok", "skipped_no_changes"}
        cur = sqlite3.connect(str(store.db_path))
        try:
            count = cur.execute(
                "SELECT COUNT(*) FROM evidence_tombstones WHERE path = ?", ("t.py",)
            ).fetchone()[0]
        finally:
            cur.close()
        assert count >= 1
    finally:
        store.close()


def test_move_path_reindexes_with_normalized_paths(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        (workspace / "sub").mkdir()
        (workspace / "a.py").rename(workspace / "sub" / "helper.py")
        result = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert result.status in {"ok", "skipped_no_changes"}
        old_row = store.get_file("a.py")
        assert old_row is None
        new_row = store.get_file("sub/helper.py")
        assert new_row is not None and new_row.is_deleted is False
        symbols = list(store.iter_symbols("sub/helper.py"))
        assert [s.qualified_name for s in symbols] == ["helper.hello"]
    finally:
        store.close()


def test_copy_path_reindexes_with_fresh_destination(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        (workspace / "b_copy.py").write_bytes((workspace / "b.py").read_bytes())
        result = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert result.status == "ok"
        old_row = store.get_file("b.py")
        assert old_row is not None and old_row.is_deleted is False
        new_row = store.get_file("b_copy.py")
        assert new_row is not None and new_row.is_deleted is False
        cur = sqlite3.connect(str(store.db_path))
        try:
            chunk_count = cur.execute(
                "SELECT COUNT(*) FROM chunks WHERE path = ?", ("b_copy.py",)
            ).fetchone()[0]
            fts_count = cur.execute(
                "SELECT COUNT(*) FROM chunks_fts WHERE path = ?", ("b_copy.py",)
            ).fetchone()[0]
        finally:
            cur.close()
        assert chunk_count > 0
        assert fts_count > 0
    finally:
        store.close()


def test_move_with_identical_content_is_a_path_pivot(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        original = (workspace / "a.py").read_bytes()
        (workspace / "moved.py").write_bytes(original)
        (workspace / "a.py").unlink()
        result = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert result.status in {"ok", "skipped_no_changes"}
        old_row = store.get_file("a.py")
        assert old_row is None
        new_row = store.get_file("moved.py")
        assert new_row is not None and new_row.is_deleted is False
        result2 = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert result2.parse_count == 0
    finally:
        store.close()


@pytest.mark.timeout_seconds(3)
def test_swap_post_publish_cancel_reports_success_published_generation(
    tmp_path: Path,
) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        first = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert first.status == "ok"
        prior_generation_str = store.get_setting("current_generation")
        assert prior_generation_str is not None

        cancel_state = {"n": 0}

        def late_cancel() -> bool:
            cancel_state["n"] += 1
            return cancel_state["n"] >= 50

        result = reindex(
            store,
            workspace,
            options=ReindexOptions(
                mode="full",
                timeout_ms=DEFAULT_TIMEOUT_MS,
            ),
            cancel=late_cancel,
        )

        assert result.status == "ok", (
            f"cancel after the main-DB swap must not roll back the "
            f"successful publish; got {result.status!r} "
            f"(error_summary={result.error_summary!r}, "
            f"cancel_polls={cancel_state['n']})"
        )
        files_rows = _count_files_rows(store)
        assert files_rows >= 3
        assert prior_generation_str is not None
        assert cancel_state["n"] >= 5
    finally:
        store.close()


@pytest.mark.timeout_seconds(3)
def test_swap_post_publish_deadline_reports_success_published_generation(
    tmp_path: Path,
) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        first = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert first.status == "ok"

        clock = _SequenceClock([1_000.0] * 50 + [1_010.0] * 50)

        result = reindex(
            store,
            workspace,
            options=ReindexOptions(
                mode="full",
                timeout_ms=DEFAULT_TIMEOUT_MS,
                clock=clock,
            ),
        )

        assert result.status == "ok", (
            f"deadline after the main-DB swap must not downgrade "
            f"the published result; got {result.status!r} "
            f"(error_summary={result.error_summary!r})"
        )
        assert _count_files_rows(store) >= 3
    finally:
        store.close()


def test_copy_populates_content_cache(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert store.content_cache_size() > 0
        file_hashes = {row.content_hash for row in store.iter_files()}
        cache_rows = list(store.iter_content_cache())
        cache_hashes = {row.content_hash for row in cache_rows}
        assert cache_hashes.issubset(file_hashes)
    finally:
        store.close()


def test_copy_with_identical_content_reuses_cache(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        first = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert first.status == "ok"
        cache_size_before = store.content_cache_size()
        (workspace / "b_copy.py").write_bytes((workspace / "b.py").read_bytes())
        result = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert result.status == "ok"
        new_row = store.get_file("b_copy.py")
        assert new_row is not None and new_row.is_deleted is False
        assert store.content_cache_size() == cache_size_before
    finally:
        store.close()


def test_move_with_identical_content_uses_cache_and_pivots_path(
    tmp_path: Path,
) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        cache_size_before = store.content_cache_size()
        original_bytes = (workspace / "a.py").read_bytes()
        (workspace / "a.py").unlink()
        (workspace / "moved.py").write_bytes(original_bytes)
        result = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert result.status in {"ok", "skipped_no_changes"}
        old_row = store.get_file("a.py")
        assert old_row is None
        new_row = store.get_file("moved.py")
        assert new_row is not None and new_row.is_deleted is False
        symbols = list(store.iter_symbols("moved.py"))
        assert [s.qualified_name for s in symbols] == ["moved.hello"]
        assert store.content_cache_size() == cache_size_before
    finally:
        store.close()


def test_cache_lookup_rejects_stale_extractor_version(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        rows = list(store.iter_content_cache())
        assert rows, "expected at least one cached row after a fresh reindex"
        for row in rows:
            looked_up = store.lookup_content_cache(
                content_hash=row.content_hash,
                extractor_version="stale-version-999",
            )
            assert looked_up is None
    finally:
        store.close()


def test_copy_then_edit_repopulates_cache_with_new_hash(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        cache_size_before = store.content_cache_size()
        (workspace / "b_copy.py").write_bytes((workspace / "b.py").read_bytes())
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert store.content_cache_size() == cache_size_before
        (workspace / "b_copy.py").write_text("y = 99\n")
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert store.content_cache_size() == cache_size_before + 1
        assert store.get_file("b.py") is not None and not store.get_file("b.py").is_deleted
        assert (
            store.get_file("b_copy.py") is not None and not store.get_file("b_copy.py").is_deleted
        )
    finally:
        store.close()


def test_content_cache_row_dataclass_is_frozen() -> None:
    row = ContentCacheRow(
        content_hash="abcd" * 16,
        language="python",
        extractor_version="phase1-lexical-v1",
        extracted_at=0.0,
        extraction_status="ok",
        error_summary=None,
    )
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        type(row).__setattr__(row, "content_hash", "deadbeef" * 8)


class _BatchingProbeStore(ExploreStore):
    def __init__(self, index_dir: Path, *, busy_timeout_ms: int = 5000) -> None:
        super().__init__(index_dir, busy_timeout_ms=busy_timeout_ms)
        self.get_file_calls = 0
        self.get_file_many_calls = 0
        self.upsert_file_calls = 0
        self.upsert_file_many_calls = 0
        self.upsert_manifest_many_calls = 0
        self.upsert_manifest_many_callsites: list[int] = []
        self.peek_dirty_paths_calls = 0

    def get_file(self, path: str) -> FileRow | None:
        self.get_file_calls += 1
        return super().get_file(path)

    def get_file_many(self, paths: Sequence[str]) -> dict[str, FileRow]:
        self.get_file_many_calls += 1
        return super().get_file_many(paths)

    def upsert_file(self, row: FileRow) -> None:
        self.upsert_file_calls += 1
        super().upsert_file(row)

    def upsert_file_many(self, rows: Sequence[FileRow]) -> None:
        self.upsert_file_many_calls += 1
        super().upsert_file_many(rows)

    def upsert_manifest_many(
        self,
        paths: Sequence[str],
        content_hashes: Sequence[str],
        sizes: Sequence[int],
        mtimes: Sequence[int],
        last_seen_generation: int,
    ) -> None:
        self.upsert_manifest_many_calls += 1
        self.upsert_manifest_many_callsites.append(len(paths))
        super().upsert_manifest_many(paths, content_hashes, sizes, mtimes, last_seen_generation)

    def peek_dirty_paths(self) -> list[str]:
        self.peek_dirty_paths_calls += 1
        return super().peek_dirty_paths()


def test_warm_no_op_refresh_uses_bulk_apis(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    for i in range(12):
        (workspace / f"f{i:02d}.py").write_text(f"x = {i}\n")

    store = _BatchingProbeStore(tmp_path / ".agent" / "ralph-explore")
    try:
        cold = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert cold.status == "ok", (
            f"cold reindex failed: status={cold.status} "
            f"failed_files={cold.failed_files} error_summary={cold.error_summary}"
        )
        cold_get_file = store.get_file_calls
        cold_get_file_many = store.get_file_many_calls
        cold_upsert_file = store.upsert_file_calls
        cold_upsert_file_many = store.upsert_file_many_calls
        cold_upsert_manifest_many = store.upsert_manifest_many_calls
        cold_peek_dirty_paths = store.peek_dirty_paths_calls

        warm = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert warm.status == "skipped_no_changes"

        assert store.get_file_calls == cold_get_file, (
            f"warm path issued {store.get_file_calls - cold_get_file} per-file get_file() calls"
        )

        assert store.get_file_many_calls - cold_get_file_many <= 1, (
            f"warm path issued {store.get_file_many_calls - cold_get_file_many} bulk reads"
        )

        assert store.upsert_file_calls == cold_upsert_file, (
            f"warm path issued {store.upsert_file_calls - cold_upsert_file} per-file upsert_file() calls"
        )

        assert store.upsert_file_many_calls == cold_upsert_file_many

        warm_upsert_manifest_many = store.upsert_manifest_many_calls - cold_upsert_manifest_many
        assert warm_upsert_manifest_many == 1
        assert store.upsert_manifest_many_callsites[-1] == 12

        assert store.peek_dirty_paths_calls - cold_peek_dirty_paths == 1
    finally:
        store.close()


def test_warm_small_edit_uses_bulk_manifest_for_unchanged_paths(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    for i in range(12):
        (workspace / f"f{i:02d}.py").write_text(f"x = {i}\n")

    store = _BatchingProbeStore(tmp_path / ".agent" / "ralph-explore")
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        cold_upsert_manifest_many = store.upsert_manifest_many_calls
        cold_upsert_file = store.upsert_file_calls

        (workspace / "f00.py").write_text("x = 999\n")
        warm = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert warm.status == "ok"
        assert warm.parse_count == 1

        warm_upsert_manifest_many = store.upsert_manifest_many_calls - cold_upsert_manifest_many
        assert warm_upsert_manifest_many == 1
        assert store.upsert_manifest_many_callsites[-1] == 11

        assert store.upsert_file_calls - cold_upsert_file == 1
    finally:
        store.close()
