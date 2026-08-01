"""Observable line-window behavior for the in-memory workspace."""

from __future__ import annotations

from ralph.workspace.memory import MemoryWorkspace


def test_read_lines_regression_tail_zero_returns_no_lines() -> None:
    """S-5: zero-tail requests have the same observable window as FsWorkspace."""
    workspace = MemoryWorkspace()
    workspace.write("lines.txt", "one\ntwo\n")

    content, metadata = workspace.read_lines("lines.txt", tail=0)

    assert content == ""
    assert metadata == {"total_lines": 2, "returned_lines": 0, "truncated": True}
