"""Black-box tests for dirty-path tracking in workspace write handlers."""

from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path
from typing import Any

from ralph.mcp.explore.dirty_paths import (
    NoOpExploreIndex,
    build_sqlite_index_handle,
    mark_path,
    mark_paths,
    resolve_explore_index,
)
from ralph.mcp.explore.store import ExploreStore
from ralph.mcp.tools.workspace._write_handlers import (
    handle_append_file,
    handle_copy_file,
    handle_create_directory,
    handle_delete_path,
    handle_edit_file,
    handle_move_file,
    handle_write_file,
)


class _FakeSession:
    """Minimal session stub exposing explore_index."""

    def __init__(self, explore_index: Any | None) -> None:
        self.explore_index = explore_index

    def check_capability(self, capability: str) -> object:
        # All capabilities are allowed in tests.
        return {"status": "approved", "capability": capability}

    def check_edit_area(self, path: str) -> object:
        return {"status": "approved", "path": path}


class _Workspace:
    """In-memory workspace stub backed by a tmp_path directory tree."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def write(self, path: str, content: str) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def read(self, path: str) -> str:
        return (self.root / path).read_text()

    def append(self, path: str, content: str) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a") as fp:
            fp.write(content)

    def move(self, src: str, dest: str, *, overwrite: bool) -> None:
        s = self.root / src
        d = self.root / dest
        d.parent.mkdir(parents=True, exist_ok=True)
        s.rename(d)

    def copy(self, src: str, dest: str, *, overwrite: bool) -> None:
        s = self.root / src
        d = self.root / dest
        d.parent.mkdir(parents=True, exist_ok=True)
        d.write_bytes(s.read_bytes())

    def delete(self, path: str, *, recursive: bool) -> None:
        import shutil

        target = self.root / path
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()

    def mkdirs(self, path: str) -> None:
        (self.root / path).mkdir(parents=True, exist_ok=True)

    def exists(self, path: str) -> bool:
        return (self.root / path).exists()


def _decode(result) -> dict[str, Any]:
    payload = result.content[0].text
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return {"text": payload}


def test_handle_write_file_marks_dirty(tmp_path: Path) -> None:
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    (workspace_root / "a.py").write_text("x = 1\n")

    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        index = build_sqlite_index_handle(store)
        session = _FakeSession(index)
        result = handle_write_file(
            session,
            _Workspace(workspace_root),
            {"path": "a.py", "content": "x = 2\n"},
        )
        payload = _decode(result)
        assert payload["path"] == "a.py"
        assert payload["bytes_written"] == 6
        assert payload["status"] == "ok"
        assert payload["index_used"] is True
        # AC-04 contract: ``changed_paths`` is the prompt-exact name
        # for the list of paths this mutation just touched (was
        # ``marked_paths`` in the original draft).
        assert payload["changed_paths"] == ["a.py"]
        assert "stale_paths_count" in payload
        assert "reindex_in_progress" in payload
        # The dirty-path queue records the same path so the next
        # reindex picks it up.
        assert store.peek_dirty_paths() == ["a.py"]
    finally:
        store.close()


def test_handle_write_file_returns_plain_text_without_index(tmp_path: Path) -> None:
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    session = _FakeSession(None)
    result = handle_write_file(
        session,
        _Workspace(workspace_root),
        {"path": "a.py", "content": "x = 2\n"},
    )
    text = result.content[0].text
    # Plain-text confirmation (current behavior).
    assert "Successfully wrote" in text
    assert "a.py" in text


def test_handle_edit_file_marks_dirty(tmp_path: Path) -> None:
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    (workspace_root / "a.py").write_text("hello world\n")

    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        session = _FakeSession(build_sqlite_index_handle(store))
        result = handle_edit_file(
            session,
            _Workspace(workspace_root),
            {
                "path": "a.py",
                "edits": [{"oldText": "hello", "newText": "goodbye"}],
            },
        )
        payload = _decode(result)
        assert payload["status"] == "applied"
        assert payload["index_used"] is True
        assert store.peek_dirty_paths() == ["a.py"]
    finally:
        store.close()


def test_handle_append_file_marks_dirty(tmp_path: Path) -> None:
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    (workspace_root / "a.py").write_text("a\n")

    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        session = _FakeSession(build_sqlite_index_handle(store))
        result = handle_append_file(
            session,
            _Workspace(workspace_root),
            {"path": "a.py", "content": "b\n"},
        )
        payload = _decode(result)
        assert payload["path"] == "a.py"
        assert payload["index_used"] is True
        assert store.peek_dirty_paths() == ["a.py"]
    finally:
        store.close()


def test_handle_create_directory_marks_dirty(tmp_path: Path) -> None:
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        session = _FakeSession(build_sqlite_index_handle(store))
        result = handle_create_directory(
            session,
            _Workspace(workspace_root),
            {"path": "new_dir"},
        )
        payload = _decode(result)
        assert payload["created"] is True
        assert payload["index_used"] is True
        assert store.peek_dirty_paths() == ["new_dir"]
    finally:
        store.close()


def test_handle_move_file_marks_both_paths(tmp_path: Path) -> None:
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    (workspace_root / "a.py").write_text("x")

    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        session = _FakeSession(build_sqlite_index_handle(store))
        result = handle_move_file(
            session,
            _Workspace(workspace_root),
            {"src": "a.py", "dest": "b.py"},
        )
        payload = _decode(result)
        assert payload["src"] == "a.py"
        assert payload["dest"] == "b.py"
        # Both src and dest are dirty.
        dirty = sorted(store.peek_dirty_paths())
        assert dirty == ["a.py", "b.py"]
    finally:
        store.close()


def test_handle_copy_file_marks_dest(tmp_path: Path) -> None:
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    (workspace_root / "a.py").write_text("x")

    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        session = _FakeSession(build_sqlite_index_handle(store))
        result = handle_copy_file(
            session,
            _Workspace(workspace_root),
            {"src": "a.py", "dest": "b.py"},
        )
        payload = _decode(result)
        assert payload["dest"] == "b.py"
        assert store.peek_dirty_paths() == ["b.py"]
    finally:
        store.close()


def test_handle_delete_path_marks_dirty(tmp_path: Path) -> None:
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    (workspace_root / "a.py").write_text("x")

    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        session = _FakeSession(build_sqlite_index_handle(store))
        result = handle_delete_path(
            session,
            _Workspace(workspace_root),
            {"path": "a.py"},
        )
        payload = _decode(result)
        assert payload["deleted"] is True
        assert store.peek_dirty_paths() == ["a.py"]
    finally:
        store.close()


def test_failed_write_does_not_mark_dirty(tmp_path: Path) -> None:
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        session = _FakeSession(build_sqlite_index_handle(store))
        # No path -> InvalidParamsError before any write happens.
        from ralph.mcp.tools.coordination import InvalidParamsError

        with suppress(InvalidParamsError):
            handle_write_file(
                session,
                _Workspace(workspace_root),
                {"content": "x"},
            )
        assert store.peek_dirty_paths() == []
    finally:
        store.close()


def test_resolve_explore_index_returns_none_when_absent() -> None:
    class _S:
        pass

    assert resolve_explore_index(_S()) is None


def test_resolve_explore_index_returns_handle_when_set() -> None:
    sentinel = object()
    assert resolve_explore_index(_FakeSession(sentinel)) is sentinel


def test_noop_explore_index_mark_dirty_is_silent() -> None:
    noop = NoOpExploreIndex()
    noop.mark_dirty(["a.py"], source_tool="write_file")  # no error


def test_mark_path_helper_normalizes() -> None:
    noop = NoOpExploreIndex()
    mark_path(noop, path="./a.py", source_tool="write_file")
    mark_paths(noop, paths=["./a.py", "b.py"], source_tool="write_file")


def test_reindex_preserves_failed_dirty_paths(tmp_path: Path) -> None:
    """AC-05: failed dirty paths must remain in the queue for the next pass."""
    from ralph.mcp.explore.pipeline import (
        DEFAULT_TIMEOUT_MS,
        ReindexOptions,
        reindex,
    )

    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "good.py").write_text("x = 1\n")
    (workspace / "bad.py").write_text("y = 2\n")

    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        # First pass seeds the index.
        reindex(
            store,
            workspace,
            options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS),
        )
        # Mark both paths dirty, then make bad.py unreadable.
        store.mark_dirty("good.py", reason="write", source_tool="write_file")
        store.mark_dirty("bad.py", reason="write", source_tool="write_file")
        # Replace the bad.py path with a directory to force an
        # extraction failure during reindex.
        (workspace / "bad.py").rmdir() if (workspace / "bad.py").is_dir() else None
        (workspace / "bad.py").unlink()
        (workspace / "bad.py").mkdir()

        reindex(
            store,
            workspace,
            options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS),
        )
        # good.py is in scope and processed, so it's removed; bad.py
        # failed and stays in the queue.
        remaining = sorted(store.peek_dirty_paths())
        assert "bad.py" in remaining, (
            f"failed path should remain in queue, got {remaining!r}"
        )
        assert "good.py" not in remaining, (
            f"processed path should be removed, got {remaining!r}"
        )
    finally:
        store.close()
