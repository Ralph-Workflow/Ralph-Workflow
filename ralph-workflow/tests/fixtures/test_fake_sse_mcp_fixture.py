"""Tests for the fake SSE MCP fixture session registry."""
from __future__ import annotations

import queue

from tests.fixtures.fake_sse_mcp_helper__sessionregistry import _SessionRegistry


class TestSseMcpSessionRegistry:
    def test_create_returns_session_id_and_usable_state(self) -> None:
        registry = _SessionRegistry()
        session_id, state = registry.create()
        assert session_id
        assert isinstance(session_id, str)
        assert isinstance(state.events, queue.Queue)
        assert state.events.empty()

    def test_get_returns_none_for_unknown_session(self) -> None:
        registry = _SessionRegistry()
        assert registry.get("nonexistent-id") is None

    def test_get_returns_same_state_after_create(self) -> None:
        registry = _SessionRegistry()
        session_id, created_state = registry.create()
        assert registry.get(session_id) is created_state
