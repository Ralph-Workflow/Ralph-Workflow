"""Tests for AgentCatalog."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.catalog import AgentCatalog
from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.spec import AgentSpec
from ralph.agents.support import AgentSupport
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.parsers.agent_output_line import AgentOutputLine


class _FakeParser:
    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        return iter([])


class _FakeStrategy(BaseExecutionStrategy):
    pass


def _make_support(
    name: str,
    transport: AgentTransport = AgentTransport.GENERIC,
    cmd: str | None = None,
) -> AgentSupport:
    return AgentSupport(
        name=name,
        spec=AgentSpec(name=name, transport=transport),
        parser_factory=_FakeParser,
        strategy_factory=_FakeStrategy,
        config=AgentConfig(cmd=cmd if cmd is not None else name, transport=transport),
    )


class TestAgentCatalog:
    """Black-box tests for AgentCatalog."""

    def test_add_and_get_roundtrip(self) -> None:
        catalog = AgentCatalog()
        support = _make_support("test-agent")
        catalog.add(support)
        assert catalog.get("test-agent") is support

    def test_get_returns_none_for_unknown(self) -> None:
        catalog = AgentCatalog()
        assert catalog.get("nonexistent") is None

    def test_get_by_command(self) -> None:
        catalog = AgentCatalog()
        support = _make_support("test-agent", cmd="my-cmd")
        catalog.add(support)
        assert catalog.get("my-cmd") is support

    def test_remove(self) -> None:
        catalog = AgentCatalog()
        support = _make_support("test-agent")
        catalog.add(support)
        catalog.remove("test-agent")
        assert catalog.get("test-agent") is None

    def test_get_parser_returns_fresh_instance(self) -> None:
        catalog = AgentCatalog()
        support = _make_support("test-agent")
        catalog.add(support)
        parser1 = catalog.get_parser("test-agent")
        parser2 = catalog.get_parser("test-agent")
        assert isinstance(parser1, _FakeParser)
        assert parser1 is not parser2

    def test_get_strategy_custom_cmd_wins(self) -> None:
        catalog = AgentCatalog()
        generic = _make_support("my-generic", transport=AgentTransport.GENERIC, cmd="generic-cmd")
        claude = _make_support("my-claude", transport=AgentTransport.CLAUDE, cmd="claude-cmd")
        catalog.add(generic)
        catalog.add(claude)
        got = catalog.get_strategy(AgentTransport.GENERIC, command="claude-cmd")
        assert isinstance(got, _FakeStrategy)

    def test_get_strategy_transport_fallback(self) -> None:
        catalog = AgentCatalog()
        support = _make_support("test-agent", transport=AgentTransport.CLAUDE)
        catalog.add(support)
        got = catalog.get_strategy(AgentTransport.CLAUDE)
        assert isinstance(got, _FakeStrategy)

    def test_get_strategy_unknown_raises(self) -> None:
        catalog = AgentCatalog()
        with pytest.raises(ValueError, match="No strategy found"):
            catalog.get_strategy(AgentTransport.CODEX)

    def test_get_parser_unknown_raises(self) -> None:
        catalog = AgentCatalog()
        with pytest.raises(ValueError, match="Unknown agent"):
            catalog.get_parser("nonexistent")

    def test_duplicate_name_raises(self) -> None:
        catalog = AgentCatalog()
        catalog.add(_make_support("dup"))
        with pytest.raises(ValueError, match="already registered"):
            catalog.add(_make_support("dup"))

    def test_duplicate_command_raises(self) -> None:
        catalog = AgentCatalog()
        catalog.add(_make_support("agent-a", cmd="same-cmd"))
        with pytest.raises(ValueError, match="already registered"):
            catalog.add(_make_support("agent-b", cmd="same-cmd"))

    def test_list_agents_sorted(self) -> None:
        catalog = AgentCatalog()
        catalog.add(_make_support("z-agent"))
        catalog.add(_make_support("a-agent"))
        assert catalog.list_agents() == ("a-agent", "z-agent")

    def test_by_transport(self) -> None:
        catalog = AgentCatalog()
        catalog.add(_make_support("gen-1", transport=AgentTransport.GENERIC))
        catalog.add(_make_support("gen-2", transport=AgentTransport.GENERIC))
        catalog.add(_make_support("claude-agent", transport=AgentTransport.CLAUDE))
        generic_agents = catalog.by_transport(AgentTransport.GENERIC)
        assert len(generic_agents) == 2
        claude_agents = catalog.by_transport(AgentTransport.CLAUDE)
        assert len(claude_agents) == 1
