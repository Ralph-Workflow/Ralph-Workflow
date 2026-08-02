"""Observable line-window behavior for the in-memory workspace."""

from __future__ import annotations

import pytest

from ralph.workspace.memory import MemoryWorkspace


def test_read_lines_regression_rejects_unbounded_content_above_byte_ceiling() -> None:
    """S-4: full reads honor the same bounded-read contract as production."""
    workspace = MemoryWorkspace()
    workspace.write("large.txt", "first\nsecond\nthird\n")

    with pytest.raises(ValueError, match="File too large for read_lines"):
        workspace.read_lines("large.txt", max_bytes=10)


def test_read_lines_regression_allows_bounded_head_above_byte_ceiling() -> None:
    """S-4: a requested line window remains available above the full-read ceiling."""
    workspace = MemoryWorkspace()
    workspace.write("large.txt", "first\nsecond\nthird\n")

    content, metadata = workspace.read_lines("large.txt", head=1, max_bytes=10)

    assert content == "first\n"
    assert metadata == {"total_lines": 3, "returned_lines": 1, "truncated": True}


def test_read_lines_regression_tail_zero_returns_no_lines() -> None:
    """S-5: zero-tail requests have the same observable window as FsWorkspace."""
    workspace = MemoryWorkspace()
    workspace.write("lines.txt", "one\ntwo\n")

    content, metadata = workspace.read_lines("lines.txt", tail=0)

    assert content == ""
    assert metadata == {"total_lines": 2, "returned_lines": 0, "truncated": True}
