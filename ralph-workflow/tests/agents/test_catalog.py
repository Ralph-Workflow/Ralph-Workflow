"""Tests for AgentCatalog."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.catalog import AgentCatalog, default_catalog
from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.execution_state._factory import strategy_for_command
from ralph.agents.execution_state.generic_execution_strategy import GenericExecutionStrategy
from ralph.agents.parsers import _CUSTOM_COMMAND_REGISTRY, _PARSER_REGISTRY, get_parser
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

    def test_remove_clears_legacy_registries(self) -> None:
        catalog = default_catalog()
        support = _make_support("legacy-remove", transport=AgentTransport.GENERIC, cmd="legacy-cmd")
        catalog.add(support)
        assert "legacy-remove" in _PARSER_REGISTRY
        assert "legacy-cmd" in _CUSTOM_COMMAND_REGISTRY

        catalog.remove("legacy-remove")
        assert catalog.get("legacy-remove") is None
        assert "legacy-remove" not in _PARSER_REGISTRY
        assert "legacy-cmd" not in _CUSTOM_COMMAND_REGISTRY

    def test_remove_preserves_builtin_transport_fallback(self) -> None:
        catalog = default_catalog()
        support = _make_support("custom-strat", transport=AgentTransport.CODEX, cmd="custom-strat")
        catalog.add(support)
        catalog.remove("custom-strat")
        assert "custom-strat" not in _PARSER_REGISTRY

    def test_remove_clears_legacy_get_parser_and_strategy_for_command(self) -> None:
        catalog = default_catalog()
        support = _make_support("remove-me", transport=AgentTransport.CODEX, cmd="remove-me-cmd")
        catalog.add(support)

        assert isinstance(get_parser("remove-me"), _FakeParser)
        strat = strategy_for_command("remove-me-cmd", AgentTransport.CODEX)
        assert isinstance(strat, _FakeStrategy)

        catalog.remove("remove-me")

        assert catalog.get("remove-me") is None
        with pytest.raises(ValueError, match="Unknown parser type"):
            get_parser("remove-me")
        fallback = strategy_for_command("remove-me-cmd", AgentTransport.CODEX)
        assert isinstance(fallback, GenericExecutionStrategy)


class TestReplaceBuiltin:
    """``AgentCatalog.replace_builtin`` is the entry point used by
    :meth:`AgentRegistry.register` to install a configured
    ``[agents.<name>]`` override on top of a built-in.
    """

    def test_replace_builtin_swaps_entries_and_by_command(self) -> None:
        """``replace_builtin`` must update both ``_entries`` and ``_by_command``."""
        catalog = AgentCatalog()
        original = _make_support("pi", transport=AgentTransport.PI, cmd="pi")
        original = AgentSupport(
            name="pi",
            spec=AgentSpec(name="pi", transport=AgentTransport.PI),
            parser_factory=_FakeParser,
            strategy_factory=_FakeStrategy,
            config=AgentConfig(cmd="pi", transport=AgentTransport.PI),
            is_builtin=True,
        )
        catalog.add(original)
        assert catalog.get("pi") is original
        assert catalog.get("pi")  # by cmd too

        new_support = AgentSupport(
            name="pi",
            spec=AgentSpec(name="pi-custom", transport=AgentTransport.PI),
            parser_factory=_FakeParser,
            strategy_factory=_FakeStrategy,
            config=AgentConfig(cmd="pi-custom", transport=AgentTransport.PI),
            is_builtin=True,
        )
        catalog.replace_builtin("pi", new_support)

        # ``_entries['pi']`` must point at the override.
        found = catalog.get("pi")
        assert found is new_support
        assert found.config.cmd == "pi-custom"
        # ``_by_command`` must point at the override under the new cmd.
        assert catalog.get("pi-custom") is new_support

    def test_replace_builtin_rejects_non_builtin_replacement(self) -> None:
        catalog = AgentCatalog()
        original = AgentSupport(
            name="pi",
            spec=AgentSpec(name="pi", transport=AgentTransport.PI),
            parser_factory=_FakeParser,
            strategy_factory=_FakeStrategy,
            config=AgentConfig(cmd="pi", transport=AgentTransport.PI),
            is_builtin=True,
        )
        catalog.add(original)

        not_builtin = AgentSupport(
            name="pi",
            spec=AgentSpec(name="pi-custom", transport=AgentTransport.PI),
            parser_factory=_FakeParser,
            strategy_factory=_FakeStrategy,
            config=AgentConfig(cmd="pi-custom", transport=AgentTransport.PI),
            is_builtin=False,
        )
        with pytest.raises(ValueError, match="is_builtin"):
            catalog.replace_builtin("pi", not_builtin)

    def test_replace_builtin_rejects_non_existing_entry(self) -> None:
        catalog = AgentCatalog()
        replacement = AgentSupport(
            name="nonexistent",
            spec=AgentSpec(name="x", transport=AgentTransport.GENERIC),
            parser_factory=_FakeParser,
            strategy_factory=_FakeStrategy,
            config=AgentConfig(cmd="x", transport=AgentTransport.GENERIC),
            is_builtin=True,
        )
        with pytest.raises(ValueError, match="non-existent"):
            catalog.replace_builtin("nonexistent", replacement)

    def test_replace_builtin_rejects_non_builtin_existing_entry(self) -> None:
        catalog = AgentCatalog()
        # Add a non-built-in registration and try to replace it.
        non_builtin = _make_support("custom-agent", transport=AgentTransport.GENERIC)
        catalog.add(non_builtin)
        replacement = AgentSupport(
            name="custom-agent",
            spec=AgentSpec(name="custom-agent", transport=AgentTransport.GENERIC),
            parser_factory=_FakeParser,
            strategy_factory=_FakeStrategy,
            config=AgentConfig(cmd="custom-agent", transport=AgentTransport.GENERIC),
            is_builtin=True,
        )
        with pytest.raises(ValueError, match="non-built-in"):
            catalog.replace_builtin("custom-agent", replacement)
