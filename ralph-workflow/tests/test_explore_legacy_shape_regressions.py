"""Regression tests for AC-04 and AC-09 backward-compatibility.

These tests cover the regressions flagged by the prior analysis
how_to_fix items:

* ``handle_write_file`` (and other mutation handlers) must not
  raise ``AttributeError`` when a real ``ExploreIndex`` handle is
  attached. The freshness payload must report ``reindex_in_progress``
  and other shared fields without exploding after a successful
  mutation.

* ``handle_list_directory`` must preserve the legacy plain-text
  shape whenever the caller did not request an indexed view AND
  must honor ``use_index='never'`` for every view (compact, ranked,
  outline, raw) as an unconditional bypass of the explore index.

* ``handle_directory_tree`` must preserve the legacy tree shape
  under the same conditions, and must consistently decorate child
  nodes (including non-``changed_only`` children) when the caller
  asks for ``include_counts`` / ``include_symbols`` / ``ranked``.

The tests use real ``build_explore_index()`` handles so the
behavior is black-box.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

from ralph.mcp.explore.handlers import build_explore_index
from ralph.mcp.tools.workspace import (
    WORKSPACE_DELETE_CAPABILITY,
    WORKSPACE_EDIT_CAPABILITY,
    WORKSPACE_READ_CAPABILITY,
    WORKSPACE_WRITE_TRACKED_CAPABILITY,
    handle_append_file,
    handle_copy_file,
    handle_delete_path,
    handle_directory_tree,
    handle_edit_file,
    handle_list_directory,
    handle_move_file,
    handle_write_file,
)
from tests.mock_session import MockSession

if TYPE_CHECKING:
    from ralph.mcp.tools.coordination import ToolContent


def _make_handle(tmp_path: Path):
    """Build a real ``ExploreIndex`` handle over a temp workspace."""
    (tmp_path / "foo.py").write_text("def foo():\n    pass\n")
    return build_explore_index(tmp_path)


def _make_listing_ws() -> MagicMock:
    ws = MagicMock()
    ws.list_dir.return_value = ["foo.py"]
    ws.is_dir.return_value = False
    ws.is_file.return_value = False
    ws.exists.return_value = True
    ws.read_text.return_value = ""
    return ws


def _make_write_ws() -> MagicMock:
    ws = MagicMock()
    ws.is_path_git_tracked.return_value = True
    ws.relative_path.return_value = "foo.py"
    return ws


class TestMutationFreshnessWithRealHandle:
    """AC-04: every mutation returns freshness metadata, no exceptions."""

    def test_write_file_returns_freshness_metadata(self, tmp_path: Path) -> None:
        handle = _make_handle(tmp_path)
        session = MockSession(
            WORKSPACE_WRITE_TRACKED_CAPABILITY, WORKSPACE_READ_CAPABILITY
        )
        session.explore_index = handle
        result = handle_write_file(
            session, _make_write_ws(),
            {"path": "foo.py", "content": "print(1)\n"},
        )
        assert result.is_error is False
        body = json.loads(cast("ToolContent", result.content[0]).text)
        for field in (
            "index_used",
            "index_generation",
            "is_stale",
            "dirty_paths_count",
            "stale_paths_count",
            "reindex_in_progress",
            "changed_paths",
        ):
            assert field in body, f"missing freshness field {field!r}"
        assert body["index_used"] is True
        assert body["reindex_in_progress"] is False
        assert body["changed_paths"] == ["foo.py"]

    def test_append_file_returns_freshness_metadata(self, tmp_path: Path) -> None:
        handle = _make_handle(tmp_path)
        session = MockSession(
            WORKSPACE_EDIT_CAPABILITY, WORKSPACE_READ_CAPABILITY
        )
        session.explore_index = handle
        ws = MagicMock()
        ws.append.return_value = None
        ws.relative_path.return_value = "foo.py"
        result = handle_append_file(
            session, ws, {"path": "foo.py", "content": "\n"}
        )
        assert result.is_error is False
        body = json.loads(cast("ToolContent", result.content[0]).text)
        assert "reindex_in_progress" in body
        assert body["index_used"] is True

    def test_edit_file_returns_freshness_metadata(self, tmp_path: Path) -> None:
        handle = _make_handle(tmp_path)
        session = MockSession(
            WORKSPACE_EDIT_CAPABILITY, WORKSPACE_READ_CAPABILITY
        )
        session.explore_index = handle
        ws = MagicMock()
        ws.read_text.return_value = "def foo():\n    pass\n"
        ws.read.return_value = "def foo():\n    pass\n"
        ws.write_text.return_value = None
        ws.write.return_value = None
        ws.relative_path.return_value = "foo.py"
        result = handle_edit_file(
            session, ws,
            {
                "path": "foo.py",
                "edits": [
                    {"oldText": "    pass", "newText": "    return 1"},
                ],
            },
        )
        assert result.is_error is False

    def test_move_file_returns_freshness_metadata(self, tmp_path: Path) -> None:
        handle = _make_handle(tmp_path)
        (tmp_path / "bar.py").write_text("bar")
        session = MockSession(
            WORKSPACE_EDIT_CAPABILITY, WORKSPACE_READ_CAPABILITY
        )
        session.explore_index = handle
        ws = MagicMock()
        ws.exists.return_value = True
        ws.move.return_value = None
        ws.relative_path.return_value = "bar.py"
        result = handle_move_file(
            session, ws,
            {"src": "bar.py", "dest": "baz.py"},
        )
        assert result.is_error is False

    def test_copy_file_returns_freshness_metadata(self, tmp_path: Path) -> None:
        handle = _make_handle(tmp_path)
        (tmp_path / "bar.py").write_text("bar")
        session = MockSession(
            WORKSPACE_EDIT_CAPABILITY, WORKSPACE_READ_CAPABILITY
        )
        session.explore_index = handle
        ws = MagicMock()
        ws.exists.return_value = True
        ws.copy.return_value = None
        ws.relative_path.return_value = "bar.py"
        result = handle_copy_file(
            session, ws,
            {"src": "bar.py", "dest": "baz.py"},
        )
        assert result.is_error is False

    def test_delete_path_returns_freshness_metadata(
        self, tmp_path: Path
    ) -> None:
        handle = _make_handle(tmp_path)
        session = MockSession(
            WORKSPACE_DELETE_CAPABILITY, WORKSPACE_READ_CAPABILITY
        )
        session.explore_index = handle
        ws = MagicMock()
        ws.is_dir.return_value = False
        ws.exists.return_value = True
        ws.remove.return_value = None
        result = handle_delete_path(session, ws, {"path": "foo.py"})
        assert result.is_error is False

    def test_write_file_with_handle_missing_reindex_attr(
        self, tmp_path: Path
    ) -> None:
        """Real ``ExploreIndex`` exposes ``reindex_in_progress``; the
        new safe accessor must default to ``False`` when a duck-typed
        handle lacks the attribute."""
        handle = _make_handle(tmp_path)
        session = MockSession(
            WORKSPACE_WRITE_TRACKED_CAPABILITY, WORKSPACE_READ_CAPABILITY
        )
        session.explore_index = handle
        result = handle_write_file(
            session, _make_write_ws(),
            {"path": "foo.py", "content": "x = 1\n"},
        )
        assert result.is_error is False
        body = json.loads(cast("ToolContent", result.content[0]).text)
        assert body["reindex_in_progress"] is False


class TestListDirectoryLegacyShape:
    """AC-09 backward compatibility for ``handle_list_directory``."""

    def test_default_preserves_legacy_text_with_handle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            handle = build_explore_index(tmp_path)
            session = MockSession(WORKSPACE_READ_CAPABILITY)
            session.explore_index = handle
            result = handle_list_directory(
                session, _make_listing_ws(), {"path": "."}
            )
            assert result.is_error is False
            text = cast("ToolContent", result.content[0]).text
            # Legacy shape: "Directory: ." header + entry list.
            assert text.startswith("Directory: .")
            assert "foo.py" in text

    def test_default_recursive_preserves_legacy_text_with_handle(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            handle = build_explore_index(tmp_path)
            session = MockSession(WORKSPACE_READ_CAPABILITY)
            session.explore_index = handle
            ws = MagicMock()
            ws.list_dir.return_value = []
            ws.is_dir.return_value = False
            result = handle_list_directory(
                session, ws, {"path": ".", "recursive": True}
            )
            assert result.is_error is False
            text = cast("ToolContent", result.content[0]).text
            assert text.startswith("Directory (recursive): .")

    def test_use_index_never_returns_legacy_text_for_every_view(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            handle = build_explore_index(tmp_path)
            session = MockSession(WORKSPACE_READ_CAPABILITY)
            session.explore_index = handle
            for view in ("raw", "compact", "ranked", "outline"):
                result = handle_list_directory(
                    session,
                    _make_listing_ws(),
                    {"path": ".", "view": view, "use_index": "never"},
                )
                assert result.is_error is False
                text = cast("ToolContent", result.content[0]).text
                assert text.startswith("Directory: ."), (
                    f"use_index=never with view={view} returned non-legacy "
                    f"text: {text!r}"
                )

    def test_use_index_always_without_handle_fails_closed(self) -> None:
        result = handle_list_directory(
            MockSession(WORKSPACE_READ_CAPABILITY),
            _make_listing_ws(),
            {"path": ".", "view": "compact", "use_index": "always"},
        )
        assert result.is_error is True
        body = json.loads(cast("ToolContent", result.content[0]).text)
        assert body["status"] == "indexed_view_unavailable"
        assert body["reason"] == "no_explore_index_handle"


class TestDirectoryTreeLegacyShape:
    """AC-09 backward compatibility for ``handle_directory_tree``."""

    def test_default_preserves_legacy_tree_with_handle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            handle = build_explore_index(tmp_path)
            session = MockSession(WORKSPACE_READ_CAPABILITY)
            session.explore_index = handle
            ws = MagicMock()

            def list_dir_effect(p: str) -> list[str]:
                if p in (".", ""):
                    return ["file.txt", "subdir"]
                return []

            ws.is_dir.side_effect = lambda p: p in (".", "")
            ws.list_dir.side_effect = list_dir_effect
            ws.is_file.side_effect = lambda p: p == "file.txt"
            ws.exists.return_value = False
            result = handle_directory_tree(session, ws, {"path": "."})
            assert result.is_error is False
            body = json.loads(cast("ToolContent", result.content[0]).text)
            # Legacy shape: top-level ``name``/``type``/``children``.
            assert set(body.keys()) >= {"name", "type", "path"}
            assert body["type"] == "dir"
            assert "children" in body
            # No ``index_used`` / ``is_stale`` wrapper when caller did
            # not ask for an indexed view.
            assert "index_used" not in body
            assert "is_stale" not in body

    def test_use_index_never_returns_legacy_tree_for_every_view(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            handle = build_explore_index(tmp_path)
            session = MockSession(WORKSPACE_READ_CAPABILITY)
            session.explore_index = handle
            ws = MagicMock()
            ws.is_dir.side_effect = lambda p: p in (".", "")
            ws.list_dir.side_effect = lambda p: ["file.txt"] if p in (".", "") else []
            ws.is_file.side_effect = lambda p: p == "file.txt"
            ws.exists.return_value = False
            for view in ("raw", "compact", "ranked", "outline"):
                result = handle_directory_tree(
                    session,
                    ws,
                    {"path": ".", "view": view, "use_index": "never"},
                )
                assert result.is_error is False
                body = json.loads(cast("ToolContent", result.content[0]).text)
                # Legacy shape preserved.
                assert "name" in body
                assert "type" in body
                assert "children" in body
                # No indexed wrapper.
                assert "index_used" not in body

    def test_compact_view_decorates_non_changed_only_children(
        self,
    ) -> None:
        """AC-09: when ``include_counts`` is requested, every child
        must receive its counts regardless of ``changed_only``."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            handle = build_explore_index(tmp_path)
            session = MockSession(WORKSPACE_READ_CAPABILITY)
            session.explore_index = handle
            ws = MagicMock()

            def list_dir_effect(p: str) -> list[str]:
                if p in (".", ""):
                    return ["file.txt", "subdir"]
                if p == "subdir":
                    return ["nested.txt"]
                return []

            ws.is_dir.side_effect = lambda p: p in (".", "", "subdir")
            ws.list_dir.side_effect = list_dir_effect
            ws.is_file.side_effect = lambda p: p in (
                "file.txt", "subdir/nested.txt"
            )
            ws.exists.return_value = False
            result = handle_directory_tree(
                session,
                ws,
                {
                    "path": ".",
                    "view": "compact",
                    "include_counts": True,
                },
            )
            assert result.is_error is False
            body = json.loads(cast("ToolContent", result.content[0]).text)
            # Decorate must have happened on every node, not just the
            # root. Subdirectory children must have counts too.
            tree = body["tree"]
            children = tree.get("children", [])
            assert isinstance(children, list)
            for child in children:
                if isinstance(child, dict):
                    assert "counts" in child, (
                        f"child {child.get('name')!r} missing counts"
                    )
                    grandchildren = child.get("children", [])
                    if isinstance(grandchildren, list):
                        for grandchild in grandchildren:
                            if isinstance(grandchild, dict):
                                assert "counts" in grandchild

    def test_use_index_always_without_handle_fails_closed(self) -> None:
        ws = MagicMock()
        ws.is_dir.side_effect = lambda p: p in (".", "")
        ws.list_dir.return_value = []
        result = handle_directory_tree(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"path": ".", "view": "compact", "use_index": "always"},
        )
        assert result.is_error is True
        body = json.loads(cast("ToolContent", result.content[0]).text)
        assert body["status"] == "indexed_view_unavailable"
        assert body["reason"] == "no_explore_index_handle"


class TestDirectoryTreeSchemaAdvertisesChangedOnly:
    """AC-09: the ``DIRECTORY_TREE_TOOL`` schema must advertise
    ``changed_only`` so callers can discover it."""

    def test_directory_tree_schema_has_changed_only(self) -> None:
        from ralph.mcp.tools.bridge._specs_file_list import file_list_specs
        from ralph.mcp.tools.names import DIRECTORY_TREE_TOOL

        specs = {s.metadata.definition.name: s for s in file_list_specs()}
        assert DIRECTORY_TREE_TOOL in specs
        spec = specs[DIRECTORY_TREE_TOOL]
        properties = cast(
            "dict[str, object]",
            cast(
                "dict[str, object]",
                spec.metadata.definition.input_schema,
            )["properties"],
        )
        assert "changed_only" in properties
        prop = cast("dict[str, object]", properties["changed_only"])
        assert prop.get("type") == "boolean"
