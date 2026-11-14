from __future__ import annotations

import json
from pathlib import Path

import pytest

from ralph.mcp.explore.dirty_paths import build_sqlite_index_handle
from ralph.mcp.explore.pipeline import DEFAULT_TIMEOUT_MS, ReindexOptions, reindex
from ralph.mcp.explore.store import ExploreStore
from ralph.mcp.tools.workspace._write_handlers import handle_write_file


class _FakeSession:
    def __init__(self, explore_index: object | None) -> None:
        self.explore_index = explore_index

    def check_capability(self, capability: str) -> object:
        return {"status": "approved", "capability": capability}

    def check_edit_area(self, path: str) -> object:
        return {"status": "approved", "path": path}


class _Workspace:
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


def _decode(result) -> dict[str, object]:
    payload = result.content[0].text
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return {"text": payload}


def _seed_workspace(root: Path) -> list[str]:
    paths = ["alpha.py", "beta.py", "gamma.py"]
    for idx, path in enumerate(paths):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("value = " + str(idx) + "\n")
    return paths


@pytest.mark.timeout_seconds(5)
def test_localized_change_marks_only_changed_path(tmp_path: Path) -> None:
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

        session = _FakeSession(build_sqlite_index_handle(store))
        handle_write_file(
            session,
            _Workspace(workspace_root),
            {"path": "alpha.py", "content": "value = 99\n"},
        )
        assert store.peek_dirty_paths() == ["alpha.py"]

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

        assert store.peek_dirty_paths() == []
    finally:
        store.close()
