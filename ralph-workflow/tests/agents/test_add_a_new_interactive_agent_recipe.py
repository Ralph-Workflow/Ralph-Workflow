"""End-to-end recipe test: adding a new interactive agent.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.activity import AgentActivityKind
from ralph.agents.execution_state import (
    BaseExecutionStrategy,
    strategy_for_command,
    strategy_for_transport,
)
from ralph.agents.parsers import AgentOutputLine, get_parser
from ralph.agents.registration import get_registered_agent_support, register_agent_support
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport

from ._registration_test_utils import _isolated_registries

if TYPE_CHECKING:
    from collections.abc import Iterator


class FakeInteractiveAgentParser:
    """Pass-through parser for the new interactive agent recipe."""

    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        for line in lines:
            yield AgentOutputLine(type="output", content=line, raw=line)


class FakeInteractiveAgentStrategy(BaseExecutionStrategy):
    """Minimal custom strategy for the new interactive agent recipe."""


class TestAddANewInteractiveAgentRecipe:
    """A single register_agent_support call wires an interactive agent end-to-end."""

    def test_interactive_agent_registration_recipe(self) -> None:
        with _isolated_registries():
            registry = AgentRegistry()

            register_agent_support(
                "fake-interactive",
                transport=AgentTransport.CLAUDE_INTERACTIVE,
                parser_factory=FakeInteractiveAgentParser,
                strategy_factory=FakeInteractiveAgentStrategy,
                agent_registry=registry,
                interactive=True,
            )

            parser = get_parser("fake-interactive")
            assert isinstance(parser, FakeInteractiveAgentParser)

            strategy = strategy_for_transport(AgentTransport.CLAUDE_INTERACTIVE)
            assert isinstance(strategy, FakeInteractiveAgentStrategy)

            # Runtime command resolution also finds the registered strategy
            # when the command string matches.
            strategy = strategy_for_command(
                "fake-interactive", AgentTransport.CLAUDE_INTERACTIVE
            )
            assert isinstance(strategy, FakeInteractiveAgentStrategy)

            pair = get_registered_agent_support("fake-interactive")
            assert pair is not None
            assert isinstance(pair[0], FakeInteractiveAgentParser)
            assert isinstance(pair[1], FakeInteractiveAgentStrategy)

            parsed = list(parser.parse(iter(["hello"])))
            assert len(parsed) == 1
            signal = strategy.classify_activity_line(parsed[0].content)
            assert signal is not None
            assert signal.kind == AgentActivityKind.OUTPUT_LINE

            assert "fake-interactive" in registry.agents
            config = registry.agents["fake-interactive"]
            assert config.transport == AgentTransport.CLAUDE_INTERACTIVE
            assert config.session_flag is not None
