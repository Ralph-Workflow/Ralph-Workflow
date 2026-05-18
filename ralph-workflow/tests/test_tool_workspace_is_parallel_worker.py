"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations

from ralph.mcp.tools.workspace import (
    is_parallel_worker,
)

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestIsParallelWorker:
    def test_false_flag_returns_false(self) -> None:
        class Session:
            is_parallel_worker = False

        assert is_parallel_worker(Session()) is False

    def test_true_flag_returns_true(self) -> None:
        class Session:
            is_parallel_worker = True

        assert is_parallel_worker(Session()) is True

    def test_callable_true_returns_true(self) -> None:
        class Session:
            def is_parallel_worker(self) -> bool:
                return True

        assert is_parallel_worker(Session()) is True

    def test_callable_false_returns_false(self) -> None:
        class Session:
            def is_parallel_worker(self) -> bool:
                return False

        assert is_parallel_worker(Session()) is False

    def test_callable_raises_type_error_returns_false(self) -> None:
        class Session:
            def is_parallel_worker(self) -> bool:
                raise TypeError("not a bool")

        assert is_parallel_worker(Session()) is False

    def test_missing_attribute_returns_false(self) -> None:
        class Session:
            pass

        assert is_parallel_worker(Session()) is False
