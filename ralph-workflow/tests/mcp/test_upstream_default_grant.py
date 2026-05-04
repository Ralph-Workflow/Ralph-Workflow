"""Regression tests: UPSTREAM_TOOL_USE capability default grants for non-commit drains.

This test verifies that Capability.UPSTREAM_TOOL_USE is granted in
DEFAULT_CAPABILITIES for non-commit SessionDrains.

UPSTREAM_TOOL_USE controls whether the session can use upstream proxy
tools (ralph_upstream__<name>__<tool>). Commit-class drains are read-only
and do not receive upstream tool access.
"""

from __future__ import annotations

import pytest

from ralph.mcp.protocol.capability_mapping import Capability, SessionDrain
from ralph.prompts.template_variables import DEFAULT_CAPABILITIES

_COMMIT_CLASS_DRAINS = (
    SessionDrain.DEVELOPMENT_COMMIT,
    SessionDrain.REVIEW_COMMIT,
    SessionDrain.COMMIT,
)

_NON_COMMIT_DRAINS = [d.value for d in SessionDrain if d not in _COMMIT_CLASS_DRAINS]


class TestUpstreamToolUseDefaultGrant:
    """Verify UPSTREAM_TOOL_USE is granted by default for non-commit drains."""

    @pytest.mark.parametrize("drain_str", _NON_COMMIT_DRAINS)
    def test_upstream_tool_use_in_defaults(self, drain_str: str) -> None:
        """UPSTREAM_TOOL_USE must be granted by default for non-commit drains.

        This ensures that when an upstream crawler (like Crawl4AI) is configured,
        its tools are visible in every non-commit Ralph phase by default.
        """
        session_drain = SessionDrain(drain_str)
        caps = DEFAULT_CAPABILITIES.get(session_drain, ())
        assert Capability.UPSTREAM_TOOL_USE in caps, (
            f"SessionDrain.{session_drain.name} is missing "
            f"Capability.UPSTREAM_TOOL_USE in DEFAULT_CAPABILITIES"
        )

    @pytest.mark.parametrize("drain_str", [d.value for d in _COMMIT_CLASS_DRAINS])
    def test_commit_drains_do_not_have_upstream_in_defaults(self, drain_str: str) -> None:
        """Commit-class drains must not have UPSTREAM_TOOL_USE in defaults."""
        session_drain = SessionDrain(drain_str)
        caps = DEFAULT_CAPABILITIES.get(session_drain, ())
        assert Capability.UPSTREAM_TOOL_USE not in caps, (
            f"SessionDrain.{session_drain.name} should not have "
            "Capability.UPSTREAM_TOOL_USE in DEFAULT_CAPABILITIES "
            "(commit-class drains are restricted)"
        )
