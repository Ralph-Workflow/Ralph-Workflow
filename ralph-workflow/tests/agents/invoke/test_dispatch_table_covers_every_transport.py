"""Guard test: verify COMMAND_BUILDERS and RUNTIME_RESOLVERS cover every AgentTransport.

This test iterates every AgentTransport value and asserts both COMMAND_BUILDERS[transport]
and RUNTIME_RESOLVERS[transport] are non-None. If a future maintainer adds a new
AgentTransport value without registering both classes, this test fails with a clear
message naming the missing transport.
"""

from __future__ import annotations

import pytest

from ralph.agents.invoke._command_builders import COMMAND_BUILDERS
from ralph.agents.invoke._runtime_resolvers import RUNTIME_RESOLVERS
from ralph.config.enums import AgentTransport


class TestDispatchTableCoversEveryTransport:
    """Guard test ensuring every AgentTransport is registered in both dispatch dicts."""

    @pytest.mark.parametrize("transport", list(AgentTransport))
    def test_command_builders_has_transport(self, transport: AgentTransport) -> None:
        """Assert COMMAND_BUILDERS has an entry for every AgentTransport."""
        assert transport in COMMAND_BUILDERS, (
            f"COMMAND_BUILDERS is missing entry for AgentTransport.{transport.name}. "
            f"Please add a CommandBuilder class for this transport."
        )
        assert COMMAND_BUILDERS[transport] is not None, (
            f"COMMAND_BUILDERS[{transport.name}] is None. "
            f"Please register a CommandBuilder class for this transport."
        )

    @pytest.mark.parametrize("transport", list(AgentTransport))
    def test_runtime_resolvers_has_transport(self, transport: AgentTransport) -> None:
        """Assert RUNTIME_RESOLVERS has an entry for every AgentTransport."""
        assert transport in RUNTIME_RESOLVERS, (
            f"RUNTIME_RESOLVERS is missing entry for AgentTransport.{transport.name}. "
            f"Please add a RuntimeResolver class for this transport."
        )
        assert RUNTIME_RESOLVERS[transport] is not None, (
            f"RUNTIME_RESOLVERS[{transport.name}] is None. "
            f"Please register a RuntimeResolver class for this transport."
        )

    def test_all_transports_covered(self) -> None:
        """Assert all AgentTransport values are covered by both dispatch dicts."""
        missing_command_builders = [t.name for t in AgentTransport if t not in COMMAND_BUILDERS]
        missing_runtime_resolvers = [t.name for t in AgentTransport if t not in RUNTIME_RESOLVERS]

        if missing_command_builders:
            pytest.fail(f"COMMAND_BUILDERS is missing these transports: {missing_command_builders}")
        if missing_runtime_resolvers:
            pytest.fail(
                f"RUNTIME_RESOLVERS is missing these transports: {missing_runtime_resolvers}"
            )
