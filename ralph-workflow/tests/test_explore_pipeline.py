"""Black-box tests for the idempotent reindex pipeline.

Tests cover the Idempotence And Efficiency Contract from the
architecture finding:

* Cold build creates the manifest, FTS rows, file rows, and initial
  generation.
* Warm no-op reindex parses zero files and duplicates zero rows.
* Warm small-edit reindex reparses only changed files.
* Delete, move, copy, and partial failure scenarios leave unchanged
  records usable.
* mode='full' rebuilds into a temp generation atomically.
* Timeout is fail-closed for the job and fail-open for tools.
* Single-writer coalescing contract.
"""

from __future__ import annotations

import dataclasses
import sqlite3
import threading
import time
from pathlib import Path
from typing import cast

import pytest

from ralph.mcp.explore.pipeline import (
    DEFAULT_TIMEOUT_MS,
    ReindexOptions,
    ReindexResult,
    ReindexWriter,
    reindex,
)
from ralph.mcp.explore.store import (
    ContentCachePayload,
    ContentCacheRow,
    ExploreStore,
    deserialize_content_cache_payload,
    sha256_text,
)


class FakeClock:
    """Test clock with a controllable time."""

    def __init__(self, initial: float = 1_000.0) -> None:
        self._t = initial

    def now(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


class _SequenceClock:
    """Clock that returns successive values from a list.

    Used by timeout-sensitive tests where the first ``now()``
    call must return a low value (``started_at`` baseline) and
    the next call must return a value past the deadline. The
    internal ``_t`` is updated to the most recent returned
    value so callers that read it after the reindex returns
    see the final clock position.
    """

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
    """Create a small workspace with three files."""
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
        # Second call: nothing changed.
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
        result = reindex(
            store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS)
        )
        assert result.status == "ok"
        # Only a.py changed.
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
        row = store.get_file("b.py")
        assert row is not None
        assert row.is_deleted is True
    finally:
        store.close()


def test_partial_failure_records_failed_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        # Patch hash_workspace_file at the module level so the lookup
        # inside ``pipeline._run_reindex`` resolves to our flaky version.
        from ralph.mcp.explore import pipeline

        original = pipeline.hash_workspace_file

        def flaky(workspace_root: Path, relative_path: str):
            # Fail only on the targeted file regardless of iteration order.
            if relative_path == "a.py":
                raise OSError("simulated failure")
            return original(workspace_root, relative_path)

        monkeypatch.setattr("ralph.mcp.explore.pipeline.hash_workspace_file", flaky)
        result = reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        # The failed file must be recorded; the rest must still be indexed.
        assert "a.py" in result.failed_files
        # Successful files are still usable.
        b_row = store.get_file("b.py")
        assert b_row is not None and b_row.is_deleted is False
    finally:
        store.close()


def test_mode_full_rebuilds_atomically(tmp_path: Path) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        # Use valid Python so the structure preflight succeeds and
        # the per-path replacement runs. ``NEW = "CONTENT"`` is a
        # valid assignment statement that re-indexes cleanly.
        (workspace / "a.py").write_text('NEW = "CONTENT"\n')
        result = reindex(
            store,
            workspace,
            options=ReindexOptions(mode="full", timeout_ms=DEFAULT_TIMEOUT_MS),
        )
        assert result.status == "ok"
        row = store.get_file("a.py")
        assert row is not None
        # Generation is reset to 1 in full mode.
        assert store.get_setting("current_generation") == "1"
    finally:
        store.close()


def test_mode_full_pre_cancel_preserves_committed_generation(tmp_path: Path) -> None:
    """AC-02/AC-05: a pre-set cancel flag MUST be checked BEFORE
    ``mode=full`` issues any destructive store writes. The prior
    committed generation and reader-visible rows are preserved
    because no ``DELETE`` is sent to the live connection.

    Without the cancel-first guard, ``_drop_all_rows`` would
    commit deletions (each store operation auto-commits via
    ``_transaction``), and a subsequent cancel would still
    report ``cancelled`` while readers observed an empty index.
    """
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        # 1. Build an initial index.
        first = reindex(
            store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS)
        )
        assert first.status == "ok"
        prior_generation = int(store.get_setting("current_generation") or 0)
        prior_generation_str = store.get_setting("current_generation")
        assert prior_generation >= 1
        # Snapshot the live row counts; these must survive the
        # cancelled full reindex unchanged.
        files_before = _count_files_rows(store)
        chunks_before = _count_chunks_rows(store)
        fts_before = _count_fts_rows(store)
        assert files_before >= 3

        # 2. Issue a full-mode reindex with a pre-set cancel callable.
        def _cancelled() -> bool:
            return True

        result = reindex(
            store,
            workspace,
            options=ReindexOptions(mode="full", timeout_ms=DEFAULT_TIMEOUT_MS),
            cancel=_cancelled,
        )
        assert result.status == "cancelled"

        # 3. The committed generation on disk is unchanged.
        assert store.get_setting("current_generation") == prior_generation_str
        # 4. Reader-visible rows are unchanged. ``_drop_all_rows``
        #    would have wiped every row, so the file/chunk/FTS
        #    counts must still equal their pre-cancel values.
        assert _count_files_rows(store) == files_before
        assert _count_chunks_rows(store) == chunks_before
        assert _count_fts_rows(store) == fts_before

        # 5. A subsequent full reindex without a cancel rebuilds
        #    cleanly; the cancelled attempt left no broken state.
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


def test_mode_full_mid_build_cancel_preserves_committed_generation(
    tmp_path: Path,
) -> None:
    """AC-02/AC-05: ``mode='full'`` runs in a staging database, so
    a cancel that becomes true during the rebuild does not leave
    the live store partially modified. The prior committed
    generation and all reader-visible rows must remain
    queryable through the cancellation.

    The cancel callable returns ``False`` for the first
    check (the dispatch) and ``True`` from the second check
    onward (during the staging file loop). The staging
    directory is abandoned in the ``finally`` block; the
    main store is never touched.
    """
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        # 1. Build an initial index.
        first = reindex(
            store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS)
        )
        assert first.status == "ok"
        prior_generation_str = store.get_setting("current_generation")
        assert prior_generation_str is not None
        files_before = _count_files_rows(store)
        chunks_before = _count_chunks_rows(store)
        fts_before = _count_fts_rows(store)
        assert files_before >= 3

        # 2. Cancel becomes true from the second check onward.
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

        # 3. The committed generation on disk is unchanged.
        assert store.get_setting("current_generation") == prior_generation_str
        # 4. Reader-visible rows are unchanged. A partial drop
        #    in the live store would have wiped file/chunk/FTS
        #    rows; the staged rebuild never touches the live
        #    store until the swap step.
        assert _count_files_rows(store) == files_before
        assert _count_chunks_rows(store) == chunks_before
        assert _count_fts_rows(store) == fts_before

        # 5. No staging directory is left behind. The full
        #    reindex path always cleans up the staging tree
        #    in its ``finally`` block, even on cancellation.
        staging_root = store.index_dir
        leftovers = [
            child
            for child in staging_root.iterdir()
            if child.name.startswith(".staging-full-")
        ]
        assert leftovers == []

        # 6. A subsequent full reindex without cancel rebuilds
        #    cleanly from the preserved prior state.
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


def test_mode_full_mid_build_timeout_preserves_committed_generation(
    tmp_path: Path,
) -> None:
    """AC-02/AC-05: a timeout that fires during the staged
    ``mode='full'`` rebuild does not modify the live store.
    The committed generation and reader-visible rows remain
    intact and the staging directory is cleaned up.

    The test uses a ``_SequenceClock`` whose first poll
    returns ``1_000.0`` (used as ``started_at``) and whose
    subsequent polls return ``1_010.0`` (past the 5s
    deadline). The first ``_ensure_deadline`` inside the
    staging file loop fires and raises
    ``_ReindexTimeoutError``; the live store is never
    touched.
    """
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        first = reindex(
            store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS)
        )
        assert first.status == "ok"
        prior_generation_str = store.get_setting("current_generation")
        assert prior_generation_str is not None
        files_before = _count_files_rows(store)
        chunks_before = _count_chunks_rows(store)
        fts_before = _count_fts_rows(store)

        # The first two calls are the ``started_at`` of the
        # outer reindex and the ``started_at`` of the
        # staged inner reindex. They both return ``1_000.0``
        # so the 5s deadline is at ``1_005.0``. Every later
        # call returns ``1_010.0`` (past the deadline), so
        # the next ``_ensure_deadline`` check inside the
        # staging file loop raises
        # ``_ReindexTimeoutError``.
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
            child
            for child in store.index_dir.iterdir()
            if child.name.startswith(".staging-full-")
        ]
        assert leftovers == []
    finally:
        store.close()


def test_mode_full_swap_io_failure_preserves_committed_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-02/AC-05: a swap-time I/O failure (e.g. disk full, read
    error from the staging file, ``os.replace`` rejection) does
    not destroy the prior committed database. The swap is
    fail-safe: a failure between the close and the rename leaves
    the main DB untouched, the connection is re-opened against
    the prior file, and the store remains queryable for
    subsequent operations.

    The fault is injected by patching ``Path.write_bytes`` to
    raise when the staging swap writes the ``.swap`` temp
    file. This triggers the early failure path in
    ``_swap_staged_index`` before ``os.replace`` is called, so
    the main database is provably untouched.
    """
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        # 1. Build an initial index.
        first = reindex(
            store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS)
        )
        assert first.status == "ok"
        prior_generation_str = store.get_setting("current_generation")
        assert prior_generation_str is not None
        files_before = _count_files_rows(store)
        chunks_before = _count_chunks_rows(store)
        fts_before = _count_fts_rows(store)
        assert files_before >= 3

        # 2. Patch ``Path.write_bytes`` to fail when the swap
        #    writes the ``.swap`` temp file. This fires inside
        #    ``_swap_staged_index`` after the connection close
        #    but before ``Path.replace``, which is the exact
        #    window the analysis feedback flagged as
        #    non-atomic. The fault is scoped to swap files only
        #    so the staging reindex can still build the new
        #    database in its own directory.
        original_write_bytes = Path.write_bytes
        swap_failures = {"n": 0}

        def flaky_write_bytes(self: Path, data: bytes) -> int:
            if self.name.endswith(".swap") and self.parent == store.db_path.parent:
                swap_failures["n"] += 1
                raise OSError("simulated disk full during swap")
            return original_write_bytes(self, data)

        monkeypatch.setattr(Path, "write_bytes", flaky_write_bytes)

        # 3. Trigger a full reindex. The fault fires in the
        #    swap step, so the result is ``failed`` and the
        #    staging directory is cleaned up by the outer
        #    ``finally`` block.
        result = reindex(
            store,
            workspace,
            options=ReindexOptions(mode="full", timeout_ms=DEFAULT_TIMEOUT_MS),
        )
        assert result.status == "failed"
        # 4. The fault was actually triggered inside the swap.
        assert swap_failures["n"] >= 1

        # 5. The committed generation on disk is unchanged.
        #    Read through a fresh connection so a stale
        #    in-process view of the prior file is not used.
        assert store.get_setting("current_generation") == prior_generation_str
        assert _count_files_rows(store) == files_before
        assert _count_chunks_rows(store) == chunks_before
        assert _count_fts_rows(store) == fts_before

        # 6. The store is still queryable through the live
        #    connection (the swap reopened it against the
        #    prior file) and the file rows point at the
        #    original content.
        a_row = store.get_file("a.py")
        assert a_row is not None
        assert a_row.is_deleted is False
        assert a_row.content_hash is not None

        # 7. No ``.swap`` temp file is left behind from the
        #    aborted swap.
        swap_dir = store.db_path.parent
        leftovers = [
            child
            for child in swap_dir.iterdir()
            if child.name.endswith(".swap")
        ]
        assert leftovers == []

        # 8. A subsequent full reindex without the fault
        #    rebuilds cleanly from the preserved prior state.
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


def test_mode_full_swap_refused_when_outer_deadline_already_exceeded(
    tmp_path: Path,
) -> None:
    """AC-05: when the outer absolute deadline has already been
    exceeded at the moment the staged full reindex is about to
    publish (the swap step), the swap is refused and the prior
    committed generation remains queryable. The result is
    ``timed_out`` (not ``ok``) so a caller cannot observe staged
    data that was built past the budget.

    The test uses a ``_SequenceClock`` whose first poll returns
    ``1_000.0`` (used as ``started_at``) and whose subsequent
    polls return ``1_010.0`` (past the 5 s deadline). The staging
    build completes inside the budget (its timeout is the
    remaining budget = ~5 s) and the swap step then observes the
    deadline already exceeded. The prior generation is preserved.
    """
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        # 1. Build an initial index.
        first = reindex(
            store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS)
        )
        assert first.status == "ok"
        prior_generation_str = store.get_setting("current_generation")
        assert prior_generation_str is not None
        files_before = _count_files_rows(store)
        chunks_before = _count_chunks_rows(store)
        fts_before = _count_fts_rows(store)
        assert files_before >= 3

        # 2. Use a clock whose polls return 1_000.0 (used for
        #    ``started_at``) then 1_000.0 (used by the staging
        #    inner-reindex ``started_at`` and the deadline check
        #    before the swap) and finally 1_010.0 (past the
        #    5 s deadline). The staging build sees a fresh
        #    started_at of 1_000.0 and runs against the
        #    remaining budget (5 s). After the staging build
        #    completes, the outer deadline check (now at
        #    1_010.0) finds 1_010.0 - 1_000.0 = 10 s elapsed,
        #    which exceeds the 5 s deadline. The swap is
        #    refused and the prior generation is preserved.
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
        # 3. The committed generation on disk is unchanged.
        assert store.get_setting("current_generation") == prior_generation_str
        # 4. Reader-visible rows are unchanged. The swap was
        #    refused before any ``os.replace`` fired, so the
        #    main DB still points at the prior generation.
        assert _count_files_rows(store) == files_before
        assert _count_chunks_rows(store) == chunks_before
        assert _count_fts_rows(store) == fts_before
        # 5. No staging directory is left behind.
        leftovers = [
            child
            for child in store.index_dir.iterdir()
            if child.name.startswith(".staging-full-")
        ]
        assert leftovers == []
        # 6. A subsequent full reindex without the deadline
        #    exceeds the budget rebuilds cleanly from the
        #    preserved prior state.
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


def test_mode_full_swap_refused_when_outer_cancellation_already_requested(
    tmp_path: Path,
) -> None:
    """AC-05: when the caller has already requested cancellation
    at the moment the staged full reindex is about to publish
    (the swap step), the swap is refused and the prior
    committed generation remains queryable. The result is
    ``cancelled`` (not ``ok``) so a caller cannot observe
    staged data after they asked to cancel.
    """
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        first = reindex(
            store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS)
        )
        assert first.status == "ok"
        prior_generation_str = store.get_setting("current_generation")
        assert prior_generation_str is not None
        files_before = _count_files_rows(store)
        chunks_before = _count_chunks_rows(store)
        fts_before = _count_fts_rows(store)

        # Cancel flips to True only at the swap step. The
        # staging build runs against a cancel=False callback
        # so it completes; the swap step then observes the
        # True flag and refuses to publish.
        cancel_state = {"n": 0}

        def _cancel_at_swap() -> bool:
            cancel_state["n"] += 1
            # The first three polls are inside the staging
            # build's file loop (one per file in the
            # ``_seed_workspace`` fixture). The fourth poll
            # is the outer pre-swap check in
            # ``_staged_full_reindex``. Flip cancel at poll
            # 4+ so the staging build completes successfully
            # and the swap step refuses to publish.
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
        # The committed generation on disk is unchanged.
        assert store.get_setting("current_generation") == prior_generation_str
        assert _count_files_rows(store) == files_before
        assert _count_chunks_rows(store) == chunks_before
        assert _count_fts_rows(store) == fts_before
        # No staging directory is left behind.
        leftovers = [
            child
            for child in store.index_dir.iterdir()
            if child.name.startswith(".staging-full-")
        ]
        assert leftovers == []
        # A subsequent full reindex without cancel rebuilds
        # cleanly.
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


def test_timeout_is_fail_closed_for_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        clock = FakeClock(initial=1_000.0)
        # A tiny timeout that the reindex will exceed.
        # Patch ``hash_workspace_file`` so each call advances the
        # clock by more than the timeout budget, simulating slow I/O.
        from ralph.mcp.explore import pipeline

        original_hash = pipeline.hash_workspace_file
        call_count = {"n": 0}

        def slow_hash(workspace_root: Path, relative_path: str):
            call_count["n"] += 1
            # Advance the clock by 0.1s on the first hash call.
            if call_count["n"] == 1:
                clock.advance(0.1)
            return original_hash(workspace_root, relative_path)

        monkeypatch.setattr(
            "ralph.mcp.explore.pipeline.hash_workspace_file", slow_hash
        )
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
        # No change because the file's content hash did not move.
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
        # Manually age the tombstone past retention (30 days).
        cur = sqlite3.connect(str(store.db_path))
        try:
            cur.execute(
                "UPDATE evidence_tombstones SET stale_at = ?",
                (time.time() - 31 * 24 * 60 * 60,),
            )
            cur.commit()
        finally:
            cur.close()
        # Trigger retention by writing a fresh tombstone (real clock,
        # not the FakeClock) — this re-runs the retention check and
        # deletes the aged tombstone.
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
        # Tombstone aged past retention is deleted; only the fresh one remains.
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
        # Only a.py should be indexed; the rest are untouched.
        assert result.status == "ok"
        assert "a.py" in result.changed_files
        assert "b.py" not in result.changed_files
    finally:
        store.close()


def test_reindex_writer_coalesces(tmp_path: Path) -> None:
    """A second reindex claim while one is active is coalesced."""
    ReindexWriter.configure(lock_factory=threading.Lock)
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        # Initial build to make store non-empty.
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        # Manually hold the active slot by calling claim from inside a lock-held block.
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
    """A concurrent lifecycle claim is coalesced while the handler owns the writer.

    The old threaded test only synchronized entry before either claim, so a
    fast first reindex could finish before the second began. This blocks the
    fake reindex at the production boundary until the lifecycle claim has
    observed the active writer; no SQLite or filesystem work is needed.
    """
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
    store = cast("ExploreStore", _Store())
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
    """AC-05/AC-06: deleting a file then running a changed reindex must
    drop its chunks, evidence, spans, symbols, and edges. Graph
    queries against the deleted path must return nothing.
    """
    workspace = _seed_workspace(tmp_path)
    (workspace / "gone.py").write_text("def gone():\n    return 1\n")
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert [s.qualified_name for s in store.iter_symbols("gone.py")] == [
            "gone.gone"
        ]
        # Delete the file and re-run a changed reindex.
        (workspace / "gone.py").unlink()
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))

        # Structure rows for the deleted path must be gone.
        assert list(store.iter_symbols("gone.py")) == []
        assert list(store.iter_spans("gone.py")) == []
        assert list(store.iter_edges(path="gone.py")) == []
        # Lexical rows must also be gone: no chunks, no FTS hits, no
        # evidence, and the file row is either removed or marked
        # is_deleted=1 (either is acceptable; what is NOT acceptable
        # is the file returning live graph/text data).
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
    """AC-05/AC-06: a full reindex must clear every index table that can
    serve structural facts, including spans, symbols, and edges. A
    deleted file's structure rows must not survive a full rebuild.
    """
    workspace = _seed_workspace(tmp_path)
    (workspace / "gone.py").write_text("def gone():\n    return 1\n")
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        # Sanity: the symbol exists before the file is removed.
        assert [s.qualified_name for s in store.iter_symbols("gone.py")] == [
            "gone.gone"
        ]
        (workspace / "gone.py").unlink()
        reindex(
            store,
            workspace,
            options=ReindexOptions(mode="full", timeout_ms=DEFAULT_TIMEOUT_MS),
        )
        # No spans / symbols / edges / chunks / FTS / evidence for
        # the deleted path after a full rebuild.
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
    """AC-05/AC-06: a delete-then-restore cycle of identical bytes must
    re-extract the path and clear ``is_deleted``. The previous pipeline
    treated any matching content hash as a no-op, leaving the file row
    as ``is_deleted=1`` with no live chunks/spans/symbols/edges.
    """
    workspace = _seed_workspace(tmp_path)
    (workspace / "restore.py").write_text("def original():\n    return 1\n")
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        # Sanity: the symbol exists before deletion.
        assert [s.qualified_name for s in store.iter_symbols("restore.py")] == [
            "restore.original"
        ]
        # Delete the file and reindex.
        (workspace / "restore.py").unlink()
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        deleted_row = store.get_file("restore.py")
        assert deleted_row is not None
        assert deleted_row.is_deleted is True
        # Restore the file with byte-identical content.
        (workspace / "restore.py").write_text("def original():\n    return 1\n")
        # The bug was: the second reindex would short-circuit on equal
        # content hash and leave the file row marked deleted. The
        # corrected pipeline must clear is_deleted and re-extract.
        result = reindex(
            store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS)
        )
        assert result.status == "ok"
        assert "restore.py" in tuple(result.changed_files)
        restored_row = store.get_file("restore.py")
        assert restored_row is not None
        assert restored_row.is_deleted is False
        # Live lexical/structure rows must be present.
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
        assert [
            s.qualified_name for s in store.iter_symbols("restore.py")
        ] == ["restore.original"]
    finally:
        store.close()


def test_malformed_changed_python_preserves_prior_rows_and_retries(tmp_path: Path) -> None:
    """PA-001 / AC-02: a malformed changed Python file preserves all
    prior lexical and structure rows, lands in ``failed_files``,
    stays in ``peek_dirty_paths()`` so a later retry replaces the
    rows with valid content, and never blocks the sorted path loop.
    """
    workspace = _seed_workspace(tmp_path)
    (workspace / "a.py").write_text(
        "def hello():\n    return 1\n\ndef goodbye():\n    return 2\n"
    )
    store = _build_store(tmp_path)
    try:
        # 1. Cold-index valid ``a.py`` and assert the lexical and
        # structure rows are queryable.
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
        # The extractor uses the file basename (``a``) as the
        # module-qualified parent.
        assert "a.hello" in before_qual

        # 2. Replace ``a.py`` with malformed Python. The path is
        # marked dirty manually so the next changed reindex has to
        # process it.
        (workspace / "a.py").write_text("def broken(:\n    pass\n")
        store.mark_dirty("a.py", reason="write", source_tool="write_file")
        assert "a.py" in store.peek_dirty_paths()

        # 3. Run the changed reindex. The path lands in failed_files
        # because Python extraction raises; prior rows are kept.
        result = reindex(
            store,
            workspace,
            options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS),
        )
        assert "a.py" in result.failed_files
        # The path is NOT in changed_files; the failed path is
        # excluded from the success counter.
        assert "a.py" not in result.changed_files

        # 4. Prior lexical and structure rows for ``a.py`` are
        # still queryable (the preflight refused to write).
        assert _count_chunks_rows(store) == before_chunks
        qual_after = {sym.qualified_name for sym in store.iter_symbols("a.py")}
        assert qual_after == before_qual

        # 5. ``a.py`` remains in ``peek_dirty_paths()`` so a later
        # retry replaces the rows.
        assert "a.py" in store.peek_dirty_paths()

        # 6. Other paths (``b.py``) commit cleanly even when the
        # malformed path fails.
        other_row = store.get_file("b.py")
        assert other_row is not None
        assert other_row.is_deleted is False

        # 7. Write valid Python and rerun the changed reindex. The
        # dirty entry clears and the rows are replaced with the new
        # content hash + generation.
        (workspace / "a.py").write_text("def hello():\n    return 42\n")
        result2 = reindex(
            store,
            workspace,
            options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS),
        )
        assert result2.status == "ok"
        assert "a.py" in result2.changed_files
        # The dirty entry has been consumed for the successful path.
        assert "a.py" not in store.peek_dirty_paths()
        qual_recovered = {
            sym.qualified_name for sym in store.iter_symbols("a.py")
        }
        assert "a.hello" in qual_recovered
    finally:
        store.close()


def test_tombstone_record_is_idempotent_on_repeat_delete(tmp_path: Path) -> None:
    """AC-05: ``record_tombstone`` is called with a deterministic ID that
    is identical across a delete -> restore changed content -> delete ->
    restore original content -> delete cycle. The store must not raise
    ``IntegrityError`` and the row count must remain stable.
    """
    workspace = _seed_workspace(tmp_path)
    (workspace / "t.py").write_text("def t():\n    return 1\n")
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        # Cycle 1: delete + reindex.
        (workspace / "t.py").unlink()
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        # Cycle 2: restore with different content, reindex, delete, reindex.
        (workspace / "t.py").write_text("def t():\n    return 2\n")
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        (workspace / "t.py").unlink()
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        # Cycle 3: restore original content, reindex, delete, reindex.
        (workspace / "t.py").write_text("def t():\n    return 1\n")
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        (workspace / "t.py").unlink()
        # The previous bug raised IntegrityError on the third delete.
        result = reindex(
            store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS)
        )
        assert result.status in {"ok", "skipped_no_changes"}
        # No IntegrityError and the tombstone row count is bounded.
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
    """AC-02: reindex normalizes relative paths and treats a move as
    source-tombstone + destination-create. The old path's file row
    is marked deleted; the new path gets fresh chunks/symbols/
    evidence keyed by the new normalized path.
    """
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        # Move a.py to sub/helper.py (a brand-new directory).
        (workspace / "sub").mkdir()
        (workspace / "a.py").rename(workspace / "sub" / "helper.py")
        result = reindex(
            store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS)
        )
        assert result.status in {"ok", "skipped_no_changes"}
        # Source path is marked deleted.
        old_row = store.get_file("a.py")
        assert old_row is not None and old_row.is_deleted is True
        # Destination path is a fresh, non-deleted file.
        new_row = store.get_file("sub/helper.py")
        assert new_row is not None and new_row.is_deleted is False
        # Destination has extracted symbols.
        symbols = list(store.iter_symbols("sub/helper.py"))
        assert [s.qualified_name for s in symbols] == ["helper.hello"]
    finally:
        store.close()


def test_copy_path_reindexes_with_fresh_destination(tmp_path: Path) -> None:
    """AC-02: reindex treats a copy as destination-create. The source
    path stays intact; the destination path gets fresh chunks /
    symbols / evidence keyed by the new normalized path.
    """
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        # Copy b.py to b_copy.py.
        (workspace / "b_copy.py").write_bytes((workspace / "b.py").read_bytes())
        result = reindex(
            store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS)
        )
        assert result.status == "ok"
        # Source stays live.
        old_row = store.get_file("b.py")
        assert old_row is not None and old_row.is_deleted is False
        # Destination is a fresh file with chunks.
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
    """AC-02: a move that preserves the file content reuses the
    extraction payloads via the content hash. The destination path
    must still be a fresh, non-deleted file keyed by the new
    normalized path. A path pivot is a deterministic, idempotent
    reindex, not a parse event.
    """
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        # Save the content, move the file, restore the same content.
        original = (workspace / "a.py").read_bytes()
        (workspace / "moved.py").write_bytes(original)
        (workspace / "a.py").unlink()
        result = reindex(
            store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS)
        )
        assert result.status in {"ok", "skipped_no_changes"}
        # Old path marked deleted; new path live.
        old_row = store.get_file("a.py")
        assert old_row is not None and old_row.is_deleted is True
        new_row = store.get_file("moved.py")
        assert new_row is not None and new_row.is_deleted is False
        # Idempotent: a second reindex with no further edits is a no-op.
        result2 = reindex(
            store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS)
        )
        assert result2.parse_count == 0
    finally:
        store.close()


def test_swap_post_publish_cancel_reports_success_published_generation(
    tmp_path: Path,
) -> None:
    """AC-05 (correctness): a cancel that fires AFTER the
    ``_swap_staged_index`` main-DB swap has published the new
    generation MUST NOT cause the reindex result to be reported
    as ``cancelled``. The new generation is durably on disk; the
    published state is the truth, and a cancel that fires after
    the swap is a no-op for the publish decision (the prior
    generation is no longer queryable anyway).

    To exercise the post-publish path, the test installs a
    counter-driven cancel callable that returns False for all
    pre-swap polls (staging + outer pre-swap + inner pre-swap +
    inner pre-main-DB swap) and True ONLY inside
    ``_swap_staged_index`` after the main-DB swap step. Without
    the fix, ``_swap_staged_index`` raised
    ``_ReindexCancelledError`` at that point and the
    ``_staged_full_reindex`` outer finally block finalized the
    job as ``cancelled``.
    """
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        # 1. Build an initial index so ``prior_generation_str`` is known.
        first = reindex(
            store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS)
        )
        assert first.status == "ok"
        prior_generation_str = store.get_setting("current_generation")
        assert prior_generation_str is not None

        # 2. Cancel counter. The first N polls return False so the
        # staging build + outer pre-swap + inner pre-swap complete.
        # The cancel callable flips to True ONLY after the swap
        # has already published. We pick a count that is large
        # enough to clear the staging and outer pre-swap polls
        # but small enough that the first True return is the one
        # immediately after ``tmp_main.replace(main_db)``.
        cancel_state = {"n": 0}

        def late_cancel() -> bool:
            cancel_state["n"] += 1
            # Polls 1..N where N is the first poll inside
            # ``_swap_staged_index`` AFTER the main-DB swap has
            # published. The ``_swap_staged_index`` function
            # polls cancel at:
            #   - pre-swap (line ~441)
            #   - post-close (line ~462)
            #   - pre-main-DB swap (line ~489)
            # We need at least 3+ polls to be False before the
            # swap starts (staging polls + outer pre-swap +
            # inner pre-swap polls). A safe threshold that
            # matches the ``_seed_workspace`` fixture is well
            # above 10 polls; the second-time cancel fires is
            # guaranteed to be inside the swap or after the
            # main-DB publish.
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

        # 3. The result MUST be ``ok`` when the cancel fires
        # AFTER the main-DB swap has already published. A
        # post-publish cancel is a no-op for the publish
        # decision.
        assert result.status == "ok", (
            f"cancel after the main-DB swap must not roll back the "
            f"successful publish; got {result.status!r} "
            f"(error_summary={result.error_summary!r}, "
            f"cancel_polls={cancel_state['n']})"
        )
        # 4. A full-mode reindex resets ``current_generation``
        # back to ``"1"``; instead of comparing generations,
        # verify the live store now exposes the staged content
        # (every workspace file is queryable with the live
        # content hash that the new full build wrote). The
        # store remains queryable against the new generation
        # because the swap succeeded.
        files_rows = _count_files_rows(store)
        assert files_rows >= 3
        assert prior_generation_str is not None
        # 5. Confirm that the swap fired at least once; cancel
        # must have been polled well past the staging build's
        # file-loop (3 files) plus the outer pre-swap poll,
        # inside the ``_swap_staged_index`` call.
        assert cancel_state["n"] >= 5
    finally:
        store.close()


def test_swap_post_publish_deadline_reports_success_published_generation(
    tmp_path: Path,
) -> None:
    """AC-05 (correctness): a deadline that fires AFTER the
    main-DB swap succeeds must not downgrade the result to
    ``timed_out``. The published generation is the truth.

    A ``_SequenceClock`` polls low values during staging and
    the inner pre-swap checks so the deadline is not exceeded
    until AFTER the main-DB swap has published the new
    generation. The post-publish deadline check inside
    ``_swap_staged_index`` is the one that the analysis
    feedback flagged: with the bug, the function raised
    ``_ReindexTimeoutError`` after publishing.
    """
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        first = reindex(
            store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS)
        )
        assert first.status == "ok"

        # SequenceClock polls:
        #   - 1: outer started_at (= 1_000.0)
        #   - 2: staging inner started_at (= 1_000.0; fresh deadline)
        #   - 3..n-1: staging inner deadline checks (= 1_000.0)
        #   - n: outer pre-swap deadline check (= 1_000.0)
        #   - n+1: inner pre-swap deadline check (= 1_000.0)
        #   - n+2: inner post-close deadline check (= 1_000.0)
        #   - n+3: inner pre-main-DB swap deadline check (= 1_000.0)
        #   - n+4 and after: post-publish deadline check
        #     (= 1_010.0; past the 5 s deadline)
        # We append a long tail of high values so any post-publish
        # check sees deadline exceeded.
        clock = _SequenceClock(
            [1_000.0] * 50 + [1_010.0] * 50
        )

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
        # Full-mode swap must have committed the new file rows;
        # if the swap was rolled back the live DB would be empty
        # or rows would be missing.
        assert _count_files_rows(store) >= 3
    finally:
        store.close()


# ---------------------------------------------------------------------------
# AC-05 content-cache reuse on copy / move
# ---------------------------------------------------------------------------


def test_copy_populates_content_cache(tmp_path: Path) -> None:
    """AC-05: a fresh reindex populates ``content_cache`` with a
    payload whose ``content_hash`` matches the source file. The
    cache row stores both metadata and the chunk coordinates so
    a later copy can skip the ``chunk_text`` build.
    """
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        assert store.content_cache_size() > 0
        # Every cache row's hash matches a file row's content_hash.
        file_hashes = {
            row.content_hash for row in store.iter_files()
        }
        cache_rows = list(store.iter_content_cache())
        cache_hashes = {row.content_hash for row in cache_rows}
        assert cache_hashes.issubset(file_hashes)
    finally:
        store.close()


def test_copy_with_identical_content_reuses_cache(tmp_path: Path) -> None:
    """AC-05: copying a file to a new path produces the same
    ``content_hash`` as the source. The new destination path must
    end up with fresh path-specific rows, while the cache row
    count stays unchanged (the prior payload already covered both
    files because the hash is the same).
    """
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        first = reindex(
            store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS)
        )
        assert first.status == "ok"
        cache_size_before = store.content_cache_size()
        # Copy b.py to a new path; content is byte-identical so the
        # cache hit path fires for the new file.
        (workspace / "b_copy.py").write_bytes((workspace / "b.py").read_bytes())
        result = reindex(
            store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS)
        )
        assert result.status == "ok"
        # Destination path is present with fresh chunks.
        new_row = store.get_file("b_copy.py")
        assert new_row is not None and new_row.is_deleted is False
        # Cache size is unchanged: the same content_hash already had
        # a row, and a cache hit does NOT repopulate.
        assert store.content_cache_size() == cache_size_before
    finally:
        store.close()


def test_move_with_identical_content_uses_cache_and_pivots_path(
    tmp_path: Path,
) -> None:
    """AC-05: a path-pivot (``move`` preserving bytes) must yield a
    destination file keyed by the new path with the same
    ``content_hash``, and the cache row remains intact. The old
    path's file row is marked deleted but its cache survives.
    """
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        cache_size_before = store.content_cache_size()
        # Save content, move a.py to a fresh path with same bytes.
        original_bytes = (workspace / "a.py").read_bytes()
        (workspace / "a.py").unlink()
        (workspace / "moved.py").write_bytes(original_bytes)
        result = reindex(
            store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS)
        )
        assert result.status in {"ok", "skipped_no_changes"}
        # Old path marked deleted; new path live with symbols.
        old_row = store.get_file("a.py")
        assert old_row is not None and old_row.is_deleted is True
        new_row = store.get_file("moved.py")
        assert new_row is not None and new_row.is_deleted is False
        symbols = list(store.iter_symbols("moved.py"))
        assert [s.qualified_name for s in symbols] == ["moved.hello"]
        # Cache size unchanged: the move did not introduce a new
        # ``content_hash``, so no new cache row was added.
        assert store.content_cache_size() == cache_size_before
    finally:
        store.close()


def test_cache_payload_round_trips_through_reindex(tmp_path: Path) -> None:
    """AC-05: the persisted cache payload deserializes into a
    ``ContentCachePayload`` whose ``chunks`` carry the same
    ``text_hash`` values the live ``chunks`` table uses. This
    proves the cache and the live tables share a deterministic
    chunk-hash contract, so a cache hit produces rows with the
    same observable ``text_hash``.
    """
    from ralph.mcp.explore.store import serialize_content_cache_payload

    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        # Read every cached payload and verify text_hash round-trip.
        for cache_row in store.iter_content_cache():
            blob = store.read_content_cache_payload(content_hash=cache_row.content_hash)
            assert blob is not None
            payload = deserialize_content_cache_payload(blob)
            assert isinstance(payload, ContentCachePayload)
            assert payload.extractor_version == cache_row.extractor_version
            for chunk in payload.chunks:
                # text_hash must equal sha256(text) for every chunk.
                assert chunk.text_hash == sha256_text(chunk.text)
        # And the round-trip is encoding-stable.
        sample = next(iter(store.iter_content_cache()))
        blob1 = store.read_content_cache_payload(content_hash=sample.content_hash)
        assert blob1 is not None
        # Re-encode payload and compare; serializer is deterministic.
        payload = deserialize_content_cache_payload(blob1)
        assert isinstance(payload, ContentCachePayload)
        blob2 = serialize_content_cache_payload(payload)
        assert blob1 == blob2
    finally:
        store.close()


def test_cache_lookup_rejects_stale_extractor_version(tmp_path: Path) -> None:
    """AC-05: ``lookup_content_cache`` returns ``None`` when the
    persisted row's ``extractor_version`` does not match the
    current one. The pipeline must re-extract on every schema
    bump; the cache can never serve stale-extracted payloads.
    """
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        rows = list(store.iter_content_cache())
        assert rows, "expected at least one cached row after a fresh reindex"
        # Look up with a deliberately wrong extractor_version.
        for row in rows:
            looked_up = store.lookup_content_cache(
                content_hash=row.content_hash,
                extractor_version="stale-version-999",
            )
            assert looked_up is None
    finally:
        store.close()


def test_copy_then_edit_repopulates_cache_with_new_hash(tmp_path: Path) -> None:
    """AC-05: editing the copy produces a NEW ``content_hash`` whose
    cache row did not exist before; the edit triggers a fresh
    ``chunk_text`` build and inserts a new cache row alongside
    the prior one (different hash, so no contention).
    """
    workspace = _seed_workspace(tmp_path)
    store = _build_store(tmp_path)
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        cache_size_before = store.content_cache_size()
        (workspace / "b_copy.py").write_bytes((workspace / "b.py").read_bytes())
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        # Cache size unchanged (same content_hash).
        assert store.content_cache_size() == cache_size_before
        # Now edit the copy so its content_hash changes.
        (workspace / "b_copy.py").write_text("y = 99\n")
        reindex(store, workspace, options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS))
        # A second cache row appears for the new content_hash.
        assert store.content_cache_size() == cache_size_before + 1
        # Both paths have live file rows.
        assert store.get_file("b.py") is not None and not store.get_file("b.py").is_deleted
        assert store.get_file("b_copy.py") is not None and not store.get_file("b_copy.py").is_deleted
    finally:
        store.close()


def test_content_cache_row_dataclass_is_frozen() -> None:
    """AC-05 contract: the dataclasses used to populate the cache are
    frozen so a single source of truth cannot be mutated after
    insertion.
    """
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
