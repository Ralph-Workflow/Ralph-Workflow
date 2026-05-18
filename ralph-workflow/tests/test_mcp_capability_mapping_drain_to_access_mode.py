"""Tests for ralph/mcp/capability_mapping.py — MCP capability mapping layer."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.protocol.capability_mapping import (
    AccessMode,
    drain_to_access_mode,
)
from ralph.policy.loader import load_agents_policy

if TYPE_CHECKING:
    from ralph.policy.models import AgentsPolicy


def _builtin_agents_policy() -> AgentsPolicy:
    return load_agents_policy(Path("/nonexistent"))

# =============================================================================
# Helper function tests
# =============================================================================


class TestDrainToAccessMode:
    def test_development_allows_write(self) -> None:
        assert (
            drain_to_access_mode("development", _builtin_agents_policy()) == AccessMode.READ_WRITE
        )

    def test_fix_allows_write(self) -> None:
        assert drain_to_access_mode("fix", _builtin_agents_policy()) == AccessMode.READ_WRITE

    def test_planning_readonly(self) -> None:
        assert drain_to_access_mode("planning", _builtin_agents_policy()) == AccessMode.READ_ONLY

    def test_review_readonly(self) -> None:
        assert drain_to_access_mode("review", _builtin_agents_policy()) == AccessMode.READ_ONLY

    def test_analysis_readonly(self) -> None:
        assert drain_to_access_mode("analysis", _builtin_agents_policy()) == AccessMode.READ_ONLY
