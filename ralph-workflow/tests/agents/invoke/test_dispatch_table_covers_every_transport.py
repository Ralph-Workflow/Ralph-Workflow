"""Guard test: verify the 4 dispatch tables cover every AgentTransport.

This test iterates every AgentTransport value and asserts all 4 dispatch tables
are populated:

  - COMMAND_BUILDERS[transport]  (command builder axis)
  - RUNTIME_RESOLVERS[transport]  (runtime resolver axis)
  - _STRATEGY_DISPATCH[transport]  (strategy axis)
  - per-parser registry via resolve_parser_key + get_parser  (parser axis)

The audit is scoped to the dispatch tables that ARE pre-populated at
module import time.  It does NOT depend on ``default_catalog()`` (which
is empty until seeded and which built-ins do not cover for
``AgentTransport.GENERIC``).

If a future maintainer adds a new AgentTransport value without registering
all 4 axes, this test fails with a clear message naming the missing
transport.
"""

from __future__ import annotations

import pytest

from ralph.agents.execution_state._factory import _STRATEGY_DISPATCH
from ralph.agents.invoke._command_builders import COMMAND_BUILDERS
from ralph.agents.invoke._runtime_resolvers import RUNTIME_RESOLVERS
from ralph.agents.parsers import get_parser, resolve_parser_key
from ralph.config.enums import AgentTransport, JsonParserType


def _canonical_command_for_transport(transport: AgentTransport) -> str:
    """Return the canonical command string for the given transport.

    Mirrors the ``cmd`` value used by the 6 built-in agents in
    ``ralph/agents/builtin.py`` so the parser-key resolution can reach
    the per-transport built-in parser without seeding the catalog.
    """
    canonical_commands = {
        AgentTransport.CLAUDE: "claude",
        AgentTransport.CLAUDE_INTERACTIVE: "claude",
        AgentTransport.CODEX: "codex",
        AgentTransport.OPENCODE: "opencode",
        AgentTransport.NANOCODER: "nanocoder",
        AgentTransport.AGY: "agy",
        AgentTransport.GENERIC: "generic",
    }
    return canonical_commands[transport]


class TestDispatchTableCoversEveryTransport:
    """Guard test ensuring every AgentTransport is registered in all 4 dispatch tables."""

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

    @pytest.mark.parametrize("transport", list(AgentTransport))
    def test_strategy_dispatch_has_transport(self, transport: AgentTransport) -> None:
        """Assert _STRATEGY_DISPATCH has an entry for every AgentTransport.

        This is the strategy axis.  _STRATEGY_DISPATCH is module-populated
        with 7 entries, one per AgentTransport including GENERIC.  It does
        NOT depend on AgentRegistry seeding.
        """
        assert transport in _STRATEGY_DISPATCH, (
            f"_STRATEGY_DISPATCH is missing entry for AgentTransport.{transport.name}. "
            f"Please add a strategy factory for this transport."
        )
        assert _STRATEGY_DISPATCH[transport] is not None, (
            f"_STRATEGY_DISPATCH[{transport.name}] is None. "
            f"Please register a strategy factory for this transport."
        )

    @pytest.mark.parametrize("transport", list(AgentTransport))
    def test_parser_registry_has_transport(self, transport: AgentTransport) -> None:
        """Assert the per-parser registry can resolve a parser for every AgentTransport.

        Resolves the parser key via
        ``resolve_parser_key(<canonical-command-for-transport>, JsonParserType.GENERIC, transport)``
        and asserts ``get_parser(key)`` returns a parser instance.  This is
        the parser axis.  It is scoped to the dispatch-table layer
        (``_PARSER_REGISTRY``) rather than the catalog layer
        (``default_catalog()``) which is empty until seeded.
        """
        command = _canonical_command_for_transport(transport)
        key = resolve_parser_key(command, JsonParserType.GENERIC, transport)
        parser = get_parser(key)
        assert parser is not None, (
            f"get_parser({key!r}) returned None for AgentTransport.{transport.name}. "
            f"Please register a parser for this transport."
        )
        # Smoke check the parser has a parse() method (the AgentParser protocol).
        assert hasattr(parser, "parse"), (
            f"Parser for AgentTransport.{transport.name} is missing parse() method"
        )

    def test_all_transports_covered(self) -> None:
        """Assert all AgentTransport values are covered by all 4 dispatch tables."""
        missing_command_builders = [t.name for t in AgentTransport if t not in COMMAND_BUILDERS]
        missing_runtime_resolvers = [t.name for t in AgentTransport if t not in RUNTIME_RESOLVERS]
        missing_strategy_dispatch = [t.name for t in AgentTransport if t not in _STRATEGY_DISPATCH]

        if missing_command_builders:
            pytest.fail(f"COMMAND_BUILDERS is missing these transports: {missing_command_builders}")
        if missing_runtime_resolvers:
            pytest.fail(
                f"RUNTIME_RESOLVERS is missing these transports: {missing_runtime_resolvers}"
            )
        if missing_strategy_dispatch:
            pytest.fail(
                f"_STRATEGY_DISPATCH is missing these transports: {missing_strategy_dispatch}"
            )
