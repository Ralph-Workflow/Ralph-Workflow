"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations

import pytest

from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
)
from ralph.mcp.tools.workspace import (
    check_edit_area_restriction,
)

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestCheckEditAreaRestriction:
    def test_non_parallel_worker_passes(self) -> None:
        class Session:
            is_parallel_worker = False

        check_edit_area_restriction(Session(), "/any/path")

    def test_parallel_worker_without_checker_passes(self) -> None:
        class Session:
            is_parallel_worker = True

        check_edit_area_restriction(Session(), "/any/path")

    def test_parallel_worker_with_approved_checker_passes(self) -> None:
        class Session:
            is_parallel_worker = True

            def check_edit_area(self, path: str) -> bool:
                return True

        check_edit_area_restriction(Session(), "/any/path")

    def test_parallel_worker_with_denied_checker_raises(self) -> None:
        class Session:
            is_parallel_worker = True

            def check_edit_area(self, path: str) -> bool:
                return False

        with pytest.raises(CapabilityDeniedError):
            check_edit_area_restriction(Session(), "/any/path")
