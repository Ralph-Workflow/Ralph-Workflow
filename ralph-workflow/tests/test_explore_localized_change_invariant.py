"""Black-box tests for the localized-change invariant (S-4).

A single-file workspace mutation must touch ONLY the changed path's
dirty-queue row. Sibling paths that were not mutated must stay absent
from the dirty queue, and after a focused reindex only the changed
file is reprocessed while the dirty queue settles clean.

This locks in the S-5 synchronous ``_enqueue_mark`` seam: a write
handler persists the dirty mark before returning (deduped by
``_PENDING_MARKS``), so ``store.peek_dirty_paths()`` observes the
change immediately and the next ``reindex(mode='changed')`` reparses
exactly one file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ralph.mcp.explore.dirty_paths import build_sqlite_index_handle
from ralph.mcp.explore.pipeline import DEFAULT_TIMEOUT_MS, ReindexOptions, reindex
from ralph.mcp.explore.store import ExploreStore
from ralph.mcp.tools.workspace._write_handlers import handle_write_file


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


def _seed_workspace(root: Path) -> list[str]:
    """Create a 3-file workspace and return the relative paths."""
    paths = ["alpha.py", "beta.py", "gamma.py"]
    for idx, path in enumerate(paths):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("value = " + str(idx) + "\n")
    return paths


@pytest.mark.timeout_seconds(5)
def test_localized_change_marks_only_changed_path(tmp_path: Path) -> None:
    """A single-file mutation marks exactly the changed path dirty row.

    Sibling rows that share the workspace must not appear in the dirty
    queue. This exercises the S-5 synchronous ``_enqueue_mark`` seam:
    after the write handler returns, ``store.peek_dirty_paths()``
    observes the mark without waiting for a debounce drain.
    """
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    _seed_workspace(workspace_root)

    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        # Seed the index so sibling rows already exist on disk and in
        # the manifest. A freshly seeded store has an empty queue.
        reindex(
            store,
            workspace_root,
            options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS),
        )
        assert store.peek_dirty_paths() == []

        # Mutate ONE file via the write handler. The handler routes
        # through mark_path -> _enqueue_mark which persists the dirty
        # mark synchronously before returning.
        session = _FakeSession(build_sqlite_index_handle(store))
        result = handle_write_file(
            session,
            _Workspace(workspace_root),
            {"path": "alpha.py", "content": "value = 99\n"},
        )
        payload = _decode(result)
        assert payload["changed_paths"] == ["alpha.py"]

        dirty = store.peek_dirty_paths()
        assert dirty == ["alpha.py"]
        assert "beta.py" not in dirty
        assert "gamma.py" not in dirty
    finally:
        store.close()


@pytest.mark.timeout_seconds(5)
def test_unchanged_sibling_absent_after_settle(tmp_path: Path) -> None:
    """After a focused reindex only the changed file is reprocessed.

    The dirty queue settles clean: the changed path is removed (it was
    in scope and successfully re-extracted) and the unchanged siblings
    were never marked dirty, so they neither appear in the queue nor in
    the reindex changed_files set.
    """
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    _seed_workspace(workspace_root)

    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        reindex(
            store,
            workspace_root,
            options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS),
        )
        assert store.peek_dirty_paths() == []

        # Mutate alpha.py only; siblings are untouched on disk.
        session = _FakeSession(build_sqlite_index_handle(store))
        handle_write_file(
            session,
            _Workspace(workspace_root),
            {"path": "alpha.py", "content": "value = 99\n"},
        )
        assert store.peek_dirty_paths() == ["alpha.py"]

        # Focused reindex: only alpha.py content changed. Siblings are
        # skipped by the size+mtime prefilter (not dirty, unchanged
        # content) so the work stays proportional to the edit.
        result = reindex(
            store,
            workspace_root,
            options=ReindexOptions(timeout_ms=DEFAULT_TIMEOUT_MS),
        )
        assert result.status == "ok"
        assert result.parse_count == 1
        assert result.changed_files == ("alpha.py",)
        assert "beta.py" not in result.changed_files
        assert "gamma.py" not in result.changed_files

        # Dirty queue settles clean: the processed path was in scope
        # and is removed; no sibling was ever added.
        assert store.peek_dirty_paths() == []
    finally:
        store.close()
