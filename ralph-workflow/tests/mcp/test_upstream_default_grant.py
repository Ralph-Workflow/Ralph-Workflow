from __future__ import annotations

import pytest

from ralph.mcp.protocol.capability_mapping import Capability, SessionDrain
from ralph.prompts.template_variables import DEFAULT_CAPABILITIES

_COMMIT_CLASS_DRAINS = (
    SessionDrain.DEVELOPMENT_COMMIT,
    SessionDrain.REVIEW_COMMIT,
    SessionDrain.COMMIT,
)

_ALL_DRAINS = [d.value for d in SessionDrain]


class TestUpstreamToolUseDefaultGrant:
    @pytest.mark.parametrize("drain_str", _ALL_DRAINS)
    def test_upstream_tool_use_is_not_in_prompt_defaults_without_upstreams(
        self, drain_str: str
    ) -> None:
        session_drain = SessionDrain(drain_str)
        caps = DEFAULT_CAPABILITIES.get(session_drain, ())
        assert Capability.UPSTREAM_TOOL_USE not in caps, (
            f"SessionDrain.{session_drain.name} should not advertise "
            "Capability.UPSTREAM_TOOL_USE without configured upstream servers"
        )
