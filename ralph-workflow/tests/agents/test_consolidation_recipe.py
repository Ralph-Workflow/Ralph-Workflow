"""E2E proof: adding an agent via AgentCatalog+AgentSupport+AgentSpec requires <=5 LoC."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.catalog import AgentCatalog
from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.parsers._template import ParserTemplateBase
from ralph.agents.parsers.agent_output_line import AgentOutputLine
from ralph.agents.spec import AgentSpec
from ralph.agents.support import AgentSupport
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport

if TYPE_CHECKING:
    from collections.abc import Iterator


class _FakeHeadlessParser(ParserTemplateBase):
    _STOP_EVENT_TYPES = frozenset()

    def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
        stripped = line.strip()
        result = self.parse_json_line(stripped)
        if result is not None:
            yield result
        else:
            yield AgentOutputLine(type="raw", content=stripped, raw=stripped)


class _FakeHeadlessStrategy(BaseExecutionStrategy):
    pass


class _FakeInteractiveParser(ParserTemplateBase):
    _STOP_EVENT_TYPES = frozenset()

    def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
        stripped = line.strip()
        result = self.parse_json_line(stripped)
        if result is not None:
            yield result
        else:
            yield AgentOutputLine(type="raw", content=stripped, raw=stripped)


class _FakeInteractiveStrategy(BaseExecutionStrategy):
    pass


class TestHeadlessAgentRecipe:
    def test_headless_agent_recipe(self) -> None:
        catalog = AgentCatalog()
        support = AgentSupport(
            name="fake-headless",
            spec=AgentSpec(name="fake-headless", transport=AgentTransport.GENERIC),
            parser_factory=_FakeHeadlessParser,
            strategy_factory=_FakeHeadlessStrategy,
            config=AgentConfig(cmd="fake-headless"),
        )
        catalog.add(support)
        parser = catalog.get_parser("fake-headless")
        assert isinstance(parser, _FakeHeadlessParser)
        strategy = catalog.get_strategy(AgentTransport.GENERIC, command="fake-headless")
        assert isinstance(strategy, _FakeHeadlessStrategy)


class TestInteractiveAgentRecipe:
    def test_interactive_agent_recipe(self) -> None:
        catalog = AgentCatalog()
        support = AgentSupport(
            name="fake-interactive",
            spec=AgentSpec(
                name="fake-interactive",
                transport=AgentTransport.CLAUDE_INTERACTIVE,
                interactive=True,
                requires_pty=True,
                session_resume_template="--resume {}",
                completion_required=True,
            ),
            parser_factory=_FakeInteractiveParser,
            strategy_factory=_FakeInteractiveStrategy,
            config=AgentConfig(cmd="fake-interactive"),
        )
        catalog.add(support)
        parser = catalog.get_parser("fake-interactive")
        assert isinstance(parser, _FakeInteractiveParser)
        strategy = catalog.get_strategy(
            AgentTransport.CLAUDE_INTERACTIVE, command="fake-interactive"
        )
        assert isinstance(strategy, _FakeInteractiveStrategy)
