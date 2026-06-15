"""End-to-end recipe test: adding a new headless agent.

All tests use in-memory fakes — no real subprocesses, no real wall-clock waits,
no real psutil.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.activity import AgentActivityKind
from ralph.agents.execution_state import BaseExecutionStrategy, strategy_for_command
from ralph.agents.parsers import AgentOutputLine, get_parser
from ralph.agents.registration import get_registered_agent_support, register_agent_support
from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport

from ._registration_test_utils import _isolated_registries

if TYPE_CHECKING:
    from collections.abc import Iterator


class FakeAgentParser:
    """Pass-through parser for the new headless agent recipe."""

    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        for line in lines:
            yield AgentOutputLine(type="output", content=line, raw=line)


class FakeAgentStrategy(BaseExecutionStrategy):
    """Minimal custom strategy for the new headless agent recipe."""


class TestAddANewAgentRecipe:
    """A single register_agent_support call wires a headless agent end-to-end."""

    def test_headless_agent_registration_recipe(self) -> None:
        with _isolated_registries():
            registry = AgentRegistry()

            register_agent_support(
                "fake",
                transport=AgentTransport.GENERIC,
                parser_factory=FakeAgentParser,
                strategy_factory=FakeAgentStrategy,
                agent_registry=registry,
            )

            parser = get_parser("fake")
            assert isinstance(parser, FakeAgentParser)

            strategy = strategy_for_command("fake", AgentTransport.GENERIC)
            assert isinstance(strategy, FakeAgentStrategy)

            pair = get_registered_agent_support("fake")
            assert pair is not None
            assert isinstance(pair[0], FakeAgentParser)
            assert isinstance(pair[1], FakeAgentStrategy)

            parsed = list(parser.parse(iter(["hello"])))
            assert len(parsed) == 1
            signal = strategy.classify_activity_line(parsed[0].content)
            assert signal is not None
            assert signal.kind == AgentActivityKind.OUTPUT_LINE

            assert "fake" in registry.agents
            assert registry.agents["fake"].transport == AgentTransport.GENERIC
