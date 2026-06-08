"""Regression: recursive directory walks are bounded (same bug class as git hang).

A recursive ``list_directory`` / ``grep`` over a huge plain tree (e.g. a large
``vendor/`` without a ``.git`` marker) is an unbounded in-memory walk that can
block the MCP server thread for a long time, starving the agent of output. The
walks must cap entries and emit an explicit truncation marker.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.tools.workspace import _list_ops
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    import pytest


def test_recursive_list_truncates_beyond_max_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_list_ops, "_RECURSIVE_MAX_ENTRIES", 5)
    ws = MemoryWorkspace()
    for i in range(20):
        ws.write(f"f{i:02d}.txt", "x")

    out = _list_ops._list_dir_recursive_output(ws, "")

    assert "truncated" in out.lower()
    assert out.count("[FILE]") <= 6  # bounded, not all 20 emitted


def test_collect_files_recursive_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_list_ops, "_RECURSIVE_MAX_ENTRIES", 5)
    ws = MemoryWorkspace()
    for i in range(20):
        ws.write(f"f{i:02d}.txt", "x")

    collected = _list_ops._collect_files_recursive(ws, "")

    assert len(collected) <= 5


def test_recursive_list_not_truncated_for_small_tree() -> None:
    ws = MemoryWorkspace()
    for i in range(3):
        ws.write(f"f{i}.txt", "x")

    out = _list_ops._list_dir_recursive_output(ws, "")

    assert "truncated" not in out.lower()
    assert out.count("[FILE]") == 3
