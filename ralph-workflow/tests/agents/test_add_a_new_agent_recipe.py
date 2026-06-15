"""Demonstrates legacy register_agent_support and new AgentCatalog API side by side."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.catalog import AgentCatalog, default_catalog
from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.parsers._template import ParserTemplateBase
from ralph.agents.parsers.agent_output_line import AgentOutputLine
from ralph.agents.registration import register_agent_support_to_catalog
from ralph.agents.spec import AgentSpec
from ralph.agents.support import AgentSupport
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport

if TYPE_CHECKING:
    from collections.abc import Iterator


class FakeAgentParser(ParserTemplateBase):
    _STOP_EVENT_TYPES = frozenset()

    def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
        stripped = line.strip()
        result = self.parse_json_line(stripped)
        if result is not None:
            yield result
        else:
            yield AgentOutputLine(type="raw", content=stripped, raw=stripped)


class FakeAgentStrategy(BaseExecutionStrategy):
    pass


class TestAddANewAgentRecipe:
    def test_fresh_catalog_resolves_parser_and_strategy(self) -> None:
        catalog = AgentCatalog()
        support = AgentSupport(
            name="fake-headless",
            spec=AgentSpec(name="fake-headless", transport=AgentTransport.GENERIC),
            parser_factory=FakeAgentParser,
            strategy_factory=FakeAgentStrategy,
            config=AgentConfig(cmd="fake-headless"),
        )
        register_agent_support_to_catalog("fake-headless", support, catalog)

        parser = catalog.get_parser("fake-headless")
        assert isinstance(parser, FakeAgentParser)

        strategy = catalog.get_strategy(AgentTransport.GENERIC, command="fake-headless")
        assert isinstance(strategy, FakeAgentStrategy)

    def test_default_catalog_resolves_after_registration(self) -> None:
        support = AgentSupport(
            name="fake-default",
            spec=AgentSpec(name="fake-default", transport=AgentTransport.GENERIC),
            parser_factory=FakeAgentParser,
            strategy_factory=FakeAgentStrategy,
            config=AgentConfig(cmd="fake-default"),
        )
        register_agent_support_to_catalog("fake-default", support, default_catalog())

        found = default_catalog().get("fake-default")
        assert found is not None
        assert found.name == "fake-default"
