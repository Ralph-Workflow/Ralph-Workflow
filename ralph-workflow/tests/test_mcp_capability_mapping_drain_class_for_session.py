"""Tests for ralph/mcp/capability_mapping.py — MCP capability mapping layer."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.protocol.capability_mapping import (
    DrainClass,
    drain_class_for_session,
)
from ralph.policy.loader import load_agents_policy
from ralph.policy.validation import PolicyValidationError

if TYPE_CHECKING:
    from ralph.policy.models import AgentsPolicy


def _builtin_agents_policy() -> AgentsPolicy:
    return load_agents_policy(Path("/nonexistent"))

# =============================================================================
# Helper function tests
# =============================================================================


class TestDrainClassForSession:
    def test_planning(self) -> None:
        assert drain_class_for_session("planning", _builtin_agents_policy()) == DrainClass.PLANNING

    def test_development(self) -> None:
        assert (
            drain_class_for_session("development", _builtin_agents_policy())
            == DrainClass.DEVELOPMENT
        )

    def test_analysis(self) -> None:
        assert drain_class_for_session("analysis", _builtin_agents_policy()) == DrainClass.ANALYSIS

    def test_review(self) -> None:
        assert drain_class_for_session("review", _builtin_agents_policy()) == DrainClass.REVIEW

    def test_fix(self) -> None:
        assert drain_class_for_session("fix", _builtin_agents_policy()) == DrainClass.FIX

    def test_commit(self) -> None:
        assert drain_class_for_session("commit", _builtin_agents_policy()) == DrainClass.COMMIT

    def test_unknown_raises(self) -> None:
        with pytest.raises(PolicyValidationError):
            drain_class_for_session("unknown")
