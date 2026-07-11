"""Black-box tests for the SQLite+FTS5 index store."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ralph.mcp.explore.store import (
    DEFAULT_CHUNK_LINES,
    ChunkRow,
    EvidenceRow,
    ExploreStore,
    FileRow,
    chunk_text,
    collect_workspace_files,
    derive_chunk_id,
    derive_evidence_id,
    hash_workspace_file,
    iter_indexable_files,
    normalize_index_path,
    sha256_bytes,
    sha256_text,
)


def _build_store(tmp_path: Path) -> ExploreStore:
    index_dir = tmp_path / ".agent" / "ralph-explore"
    return ExploreStore(index_dir)


def test_store_creates_index_dir(tmp_path: Path) -> None:
    index_dir = tmp_path / ".agent" / "ralph-explore"
    store = ExploreStore(index_dir)
    try:
        assert index_dir.is_dir()
        assert store.db_path.is_file()
    finally:
        store.close()


def test_store_applies_wal_mode(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    try:
        cur = sqlite3.connect(str(store.db_path))
        try:
            mode = cur.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal"
        finally:
            cur.close()
    finally:
        store.close()


def test_store_creates_minimum_schema(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    try:
        cur = sqlite3.connect(str(store.db_path))
        try:
            tables = {
                row[0]
                for row in cur.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
                )
            }
        finally:
            cur.close()
    finally:
        store.close()
    assert {"files", "chunks", "evidence", "jobs", "dirty_paths"}.issubset(tables)


def test_store_creates_full_minimum_schema(tmp_path: Path) -> None:
    """AC-05: minimum schema must include every required table."""
    store = _build_store(tmp_path)
    try:
        cur = sqlite3.connect(str(store.db_path))
        try:
            tables = {
                row[0]
                for row in cur.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        finally:
            cur.close()
    finally:
        store.close()
    required = {
        "files",
        "content_cache",
        "chunks",
        "spans",
        "symbols",
        "edges",
        "evidence",
        "evidence_tombstones",
        "jobs",
        "dirty_paths",
        "manifest",
        "settings",
    }
    missing = required - tables
    assert not missing, f"Minimum schema missing tables: {sorted(missing)}"


def test_store_creates_chunks_fts_virtual_table(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    try:
        cur = sqlite3.connect(str(store.db_path))
        try:
            vtables = {
                row[0]
                for row in cur.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
                if row[0].startswith("chunks_fts")
            }
        finally:
            cur.close()
    finally:
        store.close()
    # Either 'chunks_fts' or 'chunks_fts_data'/'chunks_fts_idx' shadow names exist.
    assert any(name.startswith("chunks_fts") for name in vtables)


def test_upsert_and_get_file_roundtrip(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    try:
        row = FileRow(
            path="ralph/foo.py",
            content_hash=sha256_text("hello"),
            size_bytes=5,
            mtime_ns=12345,
            language="python",
            indexed_generation=1,
            indexed_at=0.0,
            is_deleted=False,
        )
        store.upsert_file(row)
        fetched = store.get_file("ralph/foo.py")
        assert fetched is not None
        assert fetched.content_hash == row.content_hash
        assert fetched.language == "python"
        assert fetched.indexed_generation == 1
        assert fetched.is_deleted is False
    finally:
        store.close()


def test_upsert_file_replaces_on_conflict(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    try:
        row_v1 = FileRow(
            path="ralph/foo.py",
            content_hash="a",
            size_bytes=1,
            mtime_ns=1,
            language="python",
            indexed_generation=1,
            indexed_at=0.0,
            is_deleted=False,
        )
        row_v2 = FileRow(
            path="ralph/foo.py",
            content_hash="b",
            size_bytes=2,
            mtime_ns=2,
            language="python",
            indexed_generation=2,
            indexed_at=1.0,
            is_deleted=False,
        )
        store.upsert_file(row_v1)
        store.upsert_file(row_v2)
        fetched = store.get_file("ralph/foo.py")
        assert fetched is not None
        assert fetched.content_hash == "b"
        assert fetched.indexed_generation == 2
    finally:
        store.close()


def test_iter_files_skips_deleted(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    try:
        store.upsert_file(
            FileRow(
                path="a.py",
                content_hash="x",
                size_bytes=1,
                mtime_ns=1,
                language="python",
                indexed_generation=1,
                indexed_at=0.0,
                is_deleted=False,
            )
        )
        store.upsert_file(
            FileRow(
                path="b.py",
                content_hash="y",
                size_bytes=1,
                mtime_ns=1,
                language="python",
                indexed_generation=1,
                indexed_at=0.0,
                is_deleted=True,
            )
        )
        paths = sorted(row.path for row in store.iter_files())
        assert paths == ["a.py"]
    finally:
        store.close()


def test_chunk_upsert_and_fts_search(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    try:
        chunk = ChunkRow(
            chunk_id=derive_chunk_id(
                path="a.py",
                start_line=1,
                end_line=10,
                text_hash="abc",
                extractor_version="v1",
            ),
            path="a.py",
            start_line=1,
            end_line=10,
            text_hash="abc",
            role="body",
            generation=1,
        )
        store.upsert_chunk(chunk, "def hello(): return 1\n")
        rows = store.fts_search("hello", limit=10)
        assert rows, "expected at least one FTS match"
        chunk_ids = [row["chunk_id"] for row in rows]
        assert chunk.chunk_id in chunk_ids
    finally:
        store.close()


def test_fts_search_returns_empty_for_unknown_term(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    try:
        rows = store.fts_search("nonexistent", limit=10)
        assert rows == []
    finally:
        store.close()


def test_evidence_insert_and_get_roundtrip(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    try:
        evidence_id = derive_evidence_id(
            path="a.py",
            content_hash="x",
            start_line=1,
            end_line=5,
            kind="chunk",
            extractor_version="v1",
        )
        store.insert_evidence(
            EvidenceRow(
                evidence_id=evidence_id,
                path="a.py",
                start_line=1,
                end_line=5,
                content_hash="x",
                generation=1,
                source_tool="grep_files",
                evidence_kind="chunk",
                created_at=0.0,
                is_stale=False,
            )
        )
        fetched = store.get_evidence(evidence_id)
        assert fetched is not None
        assert fetched.path == "a.py"
        assert fetched.is_stale is False
    finally:
        store.close()


def test_mark_dirty_is_idempotent(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    try:
        store.mark_dirty("a.py", reason="write", source_tool="write_file")
        store.mark_dirty("a.py", reason="write", source_tool="write_file")
        dirty = store.peek_dirty_paths()
        assert dirty == ["a.py"]
        consumed = store.consume_dirty_paths()
        assert consumed == ["a.py"]
        assert store.peek_dirty_paths() == []
    finally:
        store.close()


def test_record_job_caps_history(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    try:
        for i in range(150):
            store.record_job(
                job_id=f"job-{i}",
                generation=1,
                status="ok",
                started_at=float(i),
                finished_at=float(i) + 1.0,
                files_seen=1,
                files_changed=1,
                files_failed=0,
                error_summary=None,
            )
        cur = sqlite3.connect(str(store.db_path))
        try:
            count = cur.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        finally:
            cur.close()
        # The cap is JOB_HISTORY_CAP; allow a small slack to absorb
        # the deletion semantics but assert it never wildly exceeds.
        assert count <= 110
    finally:
        store.close()


def test_index_storage_bytes_includes_wal(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    try:
        store.upsert_file(
            FileRow(
                path="a.py",
                content_hash="x",
                size_bytes=1,
                mtime_ns=1,
                language="python",
                indexed_generation=1,
                indexed_at=0.0,
                is_deleted=False,
            )
        )
        size = store.index_storage_bytes()
        assert size >= store.db_path.stat().st_size
    finally:
        store.close()


def test_normalize_index_path_returns_posix() -> None:
    assert normalize_index_path("") == ""
    assert normalize_index_path("./a/./b/") == "a/b"


def test_normalize_index_path_rejects_absolute_paths() -> None:
    """AC-05: absolute paths must be rejected with ValueError."""
    with pytest.raises(ValueError, match="absolute path"):
        normalize_index_path("/tmp/escape")
    with pytest.raises(ValueError, match="absolute path"):
        normalize_index_path("/")


def test_normalize_index_path_rejects_parent_escapes() -> None:
    """AC-05: any path containing a ``..`` segment must be rejected."""
    for bad in ("..", "../escape", "a/../escape", "a/b/../../escape", "a/.."):
        with pytest.raises(ValueError, match="parent-escape"):
            normalize_index_path(bad)


def test_normalize_index_path_accepts_workspace_relative_paths() -> None:
    """AC-05: ordinary relative paths normalize without error."""
    assert normalize_index_path("a") == "a"
    assert normalize_index_path("a/b/c.py") == "a/b/c.py"
    assert normalize_index_path("./a/b/") == "a/b"


def test_chunk_text_splits_on_line_windows() -> None:
    chunks = chunk_text("a\nb\nc\nd", lines_per_chunk=2)
    assert chunks == [(1, 2, "a\nb"), (3, 4, "c\nd")]


def test_chunk_text_empty_returns_empty_list() -> None:
    assert chunk_text("") == []


def test_default_chunk_lines_constant() -> None:
    assert DEFAULT_CHUNK_LINES == 50


def test_sha256_helpers_are_deterministic() -> None:
    assert sha256_text("hello") == sha256_text("hello")
    assert sha256_text("hello") != sha256_text("world")
    assert sha256_bytes(b"hello") == sha256_bytes(b"hello")


def test_iter_indexable_files_skips_agent_dir(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("x = 1\n")
    (tmp_path / ".agent").mkdir()
    (tmp_path / ".agent" / "index.sqlite").write_text("x")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "mod.js").write_text("x")
    files = sorted(p.name for p in iter_indexable_files(tmp_path))
    assert files == ["main.py"]


def test_collect_workspace_files_returns_sorted(tmp_path: Path) -> None:
    (tmp_path / "b.py").write_text("b")
    (tmp_path / "a.py").write_text("a")
    rows = collect_workspace_files(tmp_path)
    assert [row[0] for row in rows] == ["a.py", "b.py"]


def test_hash_workspace_file_returns_hash_and_size(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("hello")
    content_hash, size_bytes, _ = hash_workspace_file(tmp_path, "a.py")
    assert size_bytes == 5
    assert content_hash == sha256_bytes(b"hello")


def test_hash_workspace_file_rejects_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        hash_workspace_file(tmp_path, "../outside.py")


def test_hash_workspace_file_raises_for_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        hash_workspace_file(tmp_path, "missing.py")


def test_delete_file_rows_clears_state(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    try:
        store.upsert_file(
            FileRow(
                path="a.py",
                content_hash="x",
                size_bytes=1,
                mtime_ns=1,
                language="python",
                indexed_generation=1,
                indexed_at=0.0,
                is_deleted=False,
            )
        )
        store.upsert_chunk(
            ChunkRow(
                chunk_id=derive_chunk_id(
                    path="a.py",
                    start_line=1,
                    end_line=2,
                    text_hash="x",
                    extractor_version="v1",
                ),
                path="a.py",
                start_line=1,
                end_line=2,
                text_hash="x",
                role="body",
                generation=1,
            ),
            text="hello",
        )
        store.delete_file_rows("a.py")
        assert store.get_file("a.py") is None
        rows = store.fts_search("hello", limit=10)
        assert rows == []
    finally:
        store.close()


def test_tombstone_round_trip(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    try:
        # Use a recent stale_at value that survives the 30-day retention cap.
        import time as _time
        recent = _time.time()
        store.record_tombstone(
            evidence_id="ev-1",
            path="a.py",
            start_line=1,
            end_line=5,
            content_hash="x",
            generation=1,
            stale_reason="content_changed",
            stale_at=recent,
            replacement_evidence_id="ev-2",
        )
        tombstone = store.get_tombstone("ev-1")
        assert tombstone is not None
        assert tombstone["stale_reason"] == "content_changed"
    finally:
        store.close()


def test_settings_round_trip(tmp_path: Path) -> None:
    store = _build_store(tmp_path)
    try:
        assert store.get_setting("k") is None
        store.set_setting("k", "v")
        assert store.get_setting("k") == "v"
    finally:
        store.close()
"""Black-box tests for the SQLite+FTS5 exploration store.

Includes the security regression for path-boundary rejection
sibling-prefix/symlink escapes, plus the prompt-exact minimum
schema/version invariants (AC-05).
"""


def test_hash_workspace_file_rejects_sibling_prefix_escape(tmp_path) -> None:
    """AC-05 contract: ``hash_workspace_file`` MUST reject sibling
    prefix collisions (``/tmp/ws`` vs ``/tmp/ws_evil``) and symlink
    escapes. The legacy ``str.startswith`` check is unsafe; the
    store now uses ``Path.is_relative_to`` so the escape raises.
    """
    from ralph.mcp.explore.store import hash_workspace_file

    workspace = tmp_path / "ws"
    workspace.mkdir()
    sibling = tmp_path / "ws_evil"
    sibling.mkdir()
    secret = sibling / "secret.txt"
    secret.write_text("SECRET_DATA")

    with pytest.raises(ValueError, match="escapes workspace"):
        hash_workspace_file(workspace, "../ws_evil/secret.txt")

    # Symlink escape: ws/link -> sibling/secret.txt.
    link = workspace / "link.txt"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")
    with pytest.raises(ValueError, match="escapes workspace"):
        hash_workspace_file(workspace, "link.txt")


def test_pipeline_re_extract_path_rejects_sibling_prefix_escape(tmp_path) -> None:
    """AC-05 contract: the reindex pipeline rejects sibling-prefix escapes
    in ``_re_extract_path`` (was previously a string-prefix check).
    """
    from ralph.mcp.explore.pipeline import (
        DEFAULT_TIMEOUT_MS,
        ReindexOptions,
        reindex,
    )
    from ralph.mcp.explore.store import ExploreStore

    workspace = tmp_path / "ws"
    workspace.mkdir()
    sibling = tmp_path / "ws_evil"
    sibling.mkdir()
    (sibling / "secret.txt").write_text("SECRET_DATA")

    # Trigger _re_extract_path by writing a file with a relative
    # path that escapes the workspace.
    workspace_relative_escape = "../ws_evil/secret.txt"
    # We can't make the file exist inside the workspace root, but
    # _re_extract_path first resolves and checks; the boundary
    # check runs before any read.
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        result = reindex(
            store,
            workspace,
            options=ReindexOptions(
                mode="changed",
                timeout_ms=DEFAULT_TIMEOUT_MS,
                path_scope=(workspace_relative_escape,),
            ),
        )
        # The path is rejected so the file is recorded as failed.
        assert workspace_relative_escape in list(result.failed_files) or (
            result.changed_files == ()
        )
    finally:
        store.close()


def test_indexed_grep_evidence_round_trip_resolves_via_read_file(tmp_path) -> None:
    """AC-02 contract: indexed grep evidence_ids must resolve through
    ``read_file(evidence_id=...)``. The fix inserts a real evidence
    row for each FTS chunk so the round-trip succeeds.
    """
    from ralph.mcp.explore.dirty_paths import build_sqlite_index_handle
    from ralph.mcp.explore.pipeline import ReindexOptions, reindex
    from ralph.mcp.explore.store import ExploreStore
    from ralph.mcp.tools.workspace._grep_handlers import handle_grep_files
    from ralph.mcp.tools.workspace._read_handlers import handle_read_file

    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "hello.py").write_text("def hello():\n    return 'world'\n")
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        reindex(store, workspace, options=ReindexOptions(timeout_ms=5000))
        session = _FakeGrepSession(build_sqlite_index_handle(store))
        result = handle_grep_files(
            session,
            _GrepWorkspace(workspace),
            {
                "pattern": "hello",
                "path": ".",
                "regex": False,
                "case_sensitive": False,
                "use_index": "auto",
                "return_evidence_ids": True,
            },
        )
        payload = json.loads(result.content[0].text)
        for ev_id in payload["evidence_ids"]:
            assert isinstance(ev_id, str) and ev_id
        # Now resolve the first evidence_id through read_file.
        read_result = handle_read_file(
            session,
            _GrepWorkspace(workspace),
            {"evidence_id": payload["evidence_ids"][0]},
        )
        assert read_result.is_error is False
        read_payload = json.loads(read_result.content[0].text)
        assert read_payload["evidence_id"] == payload["evidence_ids"][0]
        assert "hello.py" in read_payload["path"]
    finally:
        store.close()


class _FakeGrepSession:
    def __init__(self, explore_index=None) -> None:
        self.explore_index = explore_index

    def check_capability(self, capability: str) -> dict[str, str]:
        return {"status": "approved", "capability": capability}

    def check_edit_area(self, path: str) -> dict[str, str]:
        return {"status": "approved", "path": path}


class _GrepWorkspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    def read(self, path: str) -> str:
        return (self.root / path).read_text()

    def read_lines(self, path: str, *, start=None, end=None, head=None, tail=None):
        text = (self.root / path).read_text()
        lines = text.splitlines(keepends=True)
        if head is not None:
            selected = lines[:head]
        elif tail is not None:
            selected = lines[-tail:] if tail else []
        elif start is not None or end is not None:
            s = max(0, (start or 1) - 1)
            e = len(lines) if end is None else min(len(lines), end)
            selected = lines[s:e]
        else:
            selected = lines
        return "".join(selected), {
            "total_lines": len(lines),
            "returned_lines": len(selected),
            "truncated": False,
        }

    def iter_files(self, base: str):
        base_path = self.root / base if base else self.root
        for path in base_path.rglob("*"):
            if path.is_file():
                yield str(path.relative_to(self.root))

    def list_dir(self, base: str):
        target = self.root / base if base else self.root
        return [p.name for p in target.iterdir()]
