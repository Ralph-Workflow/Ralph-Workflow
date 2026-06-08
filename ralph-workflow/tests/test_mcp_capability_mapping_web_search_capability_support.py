"""Tests for ralph/mcp/capability_mapping.py — MCP capability mapping layer."""

from __future__ import annotations

import pytest

from ralph.mcp.protocol.capability_mapping import (
    Capability,
    SessionDrain,
)
from ralph.prompts.template_variables import CapabilitySet

# =============================================================================
# Helper function tests
# =============================================================================


class TestWebSearchCapabilitySupport:
    @pytest.mark.parametrize(
        "drain",
        [
            SessionDrain.PLANNING,
            SessionDrain.DEVELOPMENT,
            SessionDrain.DEVELOPMENT_ANALYSIS,
            SessionDrain.REVIEW,
            SessionDrain.REVIEW_ANALYSIS,
            SessionDrain.FIX,
        ],
    )
    def test_web_search_in_granted_drains(self, drain: SessionDrain) -> None:
        assert CapabilitySet.defaults_for_drain(drain).contains(Capability.WEB_SEARCH)

    @pytest.mark.parametrize(
        "drain",
        [
            SessionDrain.COMMIT,
            SessionDrain.DEVELOPMENT_COMMIT,
            SessionDrain.REVIEW_COMMIT,
        ],
    )
    def test_web_search_not_granted_to_other_drains(self, drain: SessionDrain) -> None:
        assert not CapabilitySet.defaults_for_drain(drain).contains(Capability.WEB_SEARCH)
