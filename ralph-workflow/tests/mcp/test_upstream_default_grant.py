"""Regression tests: UPSTREAM_TOOL_USE capability default grants across all drains.

This test verifies that Capability.UPSTREAM_TOOL_USE is granted in
DEFAULT_CAPABILITIES for every SessionDrain.

PROMPT.md specifies that web-access MCP endpoints must be exposed in every phase
by default. UPSTREAM_TOOL_USE controls whether the session can use upstream proxy
tools (ralph_upstream__<name>__<tool>).

This test passes because DEFAULT_CAPABILITIES was extended to include
UPSTREAM_TOOL_USE for all drains, consistent with PROMPT.md's default
exposure-first requirement.
"""

from __future__ import annotations

import pytest

from ralph.mcp.protocol.capability_mapping import Capability, SessionDrain
from ralph.prompts.template_variables import DEFAULT_CAPABILITIES


class TestUpstreamToolUseDefaultGrant:
    """Verify UPSTREAM_TOOL_USE is granted by default for all drains."""

    @pytest.mark.parametrize("drain_str", [d.value for d in SessionDrain])
    def test_upstream_tool_use_in_defaults(self, drain_str: str) -> None:
        """UPSTREAM_TOOL_USE must be granted by default for all drains.

        This ensures that when an upstream crawler (like Crawl4AI) is configured,
        its tools are visible in every Ralph phase by default.
        """
        session_drain = SessionDrain(drain_str)
        caps = DEFAULT_CAPABILITIES.get(session_drain, ())
        assert Capability.UPSTREAM_TOOL_USE in caps, (
            f"SessionDrain.{session_drain.name} is missing "
            f"Capability.UPSTREAM_TOOL_USE in DEFAULT_CAPABILITIES"
        )
