"""Tests for ralph/mcp/capability_mapping.py — MCP capability mapping layer."""

from __future__ import annotations

import pytest

from ralph.mcp.protocol.capability_mapping import (
    SessionDrain,
    coerce_session_drain,
)

# =============================================================================
# Helper function tests
# =============================================================================


class TestCoerceSessionDrain:
    def test_session_drain_passthrough(self) -> None:
        assert coerce_session_drain(SessionDrain.DEVELOPMENT) == SessionDrain.DEVELOPMENT

    def test_planning_string(self) -> None:
        assert coerce_session_drain("planning") == SessionDrain.PLANNING

    def test_development_string(self) -> None:
        assert coerce_session_drain("development") == SessionDrain.DEVELOPMENT

    def test_with_underscores(self) -> None:
        assert coerce_session_drain("development") == SessionDrain.DEVELOPMENT

    def test_case_insensitive(self) -> None:
        assert coerce_session_drain("DEVELOPMENT") == SessionDrain.DEVELOPMENT

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError):
            coerce_session_drain("unknown_drain")
