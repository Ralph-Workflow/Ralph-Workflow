from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.catalog import default_catalog
from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.execution_state._factory import _STRATEGY_DISPATCH
from ralph.agents.parsers import _CUSTOM_COMMAND_REGISTRY, _PARSER_REGISTRY
from ralph.agents.parsers._template import ParserTemplateBase
from ralph.agents.parsers.agent_output_line import AgentOutputLine
from ralph.agents.parsers.claude import ClaudeParser
from ralph.agents.parsers.pi import PiParser
from ralph.agents.registration import register_agent_support
from ralph.agents.registry import AgentRegistry
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport
from ralph.config.models import UnifiedConfig

if TYPE_CHECKING:
    from collections.abc import Iterator

_GOLDEN_PARSERS: dict[str, object] = dict(_PARSER_REGISTRY)
_GOLDEN_CUSTOM: dict[str, object] = dict(_CUSTOM_COMMAND_REGISTRY)
_GOLDEN_STRATEGIES: dict[AgentTransport, object] = dict(_STRATEGY_DISPATCH)
_GOLDEN_ENTRIES: dict[str, object] = dict(default_catalog()._entries)
_GOLDEN_BY_COMMAND: dict[str, object] = dict(default_catalog()._by_command)


def _restore_golden_catalog() -> None:
    cat = default_catalog()
    cat._entries.clear()
    cat._entries.update(_GOLDEN_ENTRIES)
    cat._by_command.clear()
    cat._by_command.update(_GOLDEN_BY_COMMAND)
    cat._state.parsers.clear()
    cat._state.parsers.update(_GOLDEN_PARSERS)
    cat._state.commands.clear()
    cat._state.commands.update(_GOLDEN_CUSTOM)
    cat._state.strategies.clear()
    cat._state.strategies.update(_GOLDEN_STRATEGIES)


@pytest.fixture(autouse=True)
def _reset_default_catalog() -> object:
    _restore_golden_catalog()
    yield
    _restore_golden_catalog()


class DummyParser(ParserTemplateBase):
    _STOP_EVENT_TYPES = frozenset()

    def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
        yield AgentOutputLine(type="raw", content=line, raw=line)


class DummyStrategy(BaseExecutionStrategy):
    pass


def test_register_unregister_flow() -> None:
    registry = AgentRegistry()
    name = "dummy-headless-agent"

    register_agent_support(
        name=name,
        transport=AgentTransport.GENERIC,
        parser_factory=DummyParser,
        strategy_factory=DummyStrategy,
        agent_registry=registry,
        interactive=False,
    )

    assert name in registry.agents
    assert default_catalog().get(name) is not None

    registry.unregister(name)

    assert name not in registry.agents
    assert default_catalog().get(name) is None


def test_idempotent_register_unregister_loop() -> None:
    registry = AgentRegistry()
    name = "dummy-loop-agent"

    for _ in range(5):
        register_agent_support(
            name=name,
            transport=AgentTransport.GENERIC,
            parser_factory=DummyParser,
            strategy_factory=DummyStrategy,
            agent_registry=registry,
            interactive=False,
        )
        assert name in registry.agents
        assert default_catalog().get(name) is not None

        registry.unregister(name)
        assert name not in registry.agents
        assert default_catalog().get(name) is None


def test_interactive_register_unregister() -> None:
    registry = AgentRegistry()
    name = "dummy-interactive-agent"

    register_agent_support(
        name=name,
        transport=AgentTransport.CLAUDE_INTERACTIVE,
        parser_factory=DummyParser,
        strategy_factory=DummyStrategy,
        agent_registry=registry,
        interactive=True,
    )
    assert name in registry.agents
    assert default_catalog().get(name) is not None
    assert default_catalog().get(name).spec.requires_pty is True

    registry.unregister(name)
    assert name not in registry.agents
    assert default_catalog().get(name) is None


def test_unregister_not_in_catalog() -> None:
    registry = AgentRegistry()
    name = "legacy-alias"
    config = AgentConfig(cmd="echo alias", transport=AgentTransport.CLAUDE)
    registry.register(name, config)

    assert name in registry.agents
    assert default_catalog().get(name) is None

    registry.unregister(name)
    assert name not in registry.agents
    assert default_catalog().get(name) is None


def test_pi_dynamic_alias_preserves_provider_id_suffix() -> None:
    registry = AgentRegistry()

    config = registry.get("pi/anthropic/claude-sonnet-4-20250514")
    assert config is not None, "pi/anthropic/claude-sonnet-4-20250514 must resolve"
    assert config.model_flag == "--model anthropic/claude-sonnet-4-20250514"

    config_with_thinking = registry.get("pi/anthropic/claude-sonnet-4-20250514:high")
    assert config_with_thinking is not None
    assert config_with_thinking.model_flag == "--model anthropic/claude-sonnet-4-20250514:high"


def test_pi_dynamic_alias_rejects_malformed_model_ids() -> None:
    registry = AgentRegistry()

    assert registry.get("pi//x") is None
    assert registry.get("pi/provider/") is None
    assert registry.get("pi//") is None
    assert registry.get("pi/") is None
    assert registry.get("pi/anthropic/") is None
    assert registry.get("pi/anthropic/claude-sonnet-4-20250514:") is None
    assert registry.get("pi/:high") is None
    assert registry.get("pi/:") is None


def test_pi_dynamic_alias_accepts_nested_provider_model_paths() -> None:
    registry = AgentRegistry()

    bare = registry.get("pi/anthropic/claude-sonnet-4-20250514")
    assert bare is not None
    assert bare.model_flag == "--model anthropic/claude-sonnet-4-20250514"

    bare_id = registry.get("pi/sonnet")
    assert bare_id is not None
    assert bare_id.model_flag == "--model sonnet"

    thinking = registry.get("pi/anthropic/claude-sonnet-4-20250514:high")
    assert thinking is not None
    assert thinking.model_flag == "--model anthropic/claude-sonnet-4-20250514:high"

    nested = registry.get("pi/provider/model/extra:high")
    assert nested is not None
    assert nested.model_flag == "--model provider/model/extra:high"

    deep = registry.get("pi/anthropic/claude/foo/bar")
    assert deep is not None
    assert deep.model_flag == "--model anthropic/claude/foo/bar"


def test_configured_pi_override_propagates_to_catalog() -> None:
    config = UnifiedConfig(
        agents={
            "pi": AgentConfig(
                cmd="pi-custom",
                transport=AgentTransport.PI,
                session_flag="--session {}",
                yolo_flag="--approve",
            )
        }
    )
    registry = AgentRegistry.from_config(config)

    direct = registry.get("pi")
    assert direct is not None, "registry.get('pi') must resolve the override"
    assert direct.cmd == "pi-custom", (
        f"registry.get('pi').cmd must be the override ('pi-custom'), got {direct.cmd!r}"
    )

    catalog_pi = registry.catalog.get("pi")
    assert catalog_pi is not None, (
        "registry.catalog.get('pi') must resolve the configured override; "
        "without this fix the catalog still reports the built-in"
    )
    assert catalog_pi.config.cmd == "pi-custom", (
        f"registry.catalog.get('pi').config.cmd must be 'pi-custom', got {catalog_pi.config.cmd!r}"
    )
    assert catalog_pi.parser_factory is PiParser, (
        f"pi override must keep the built-in's PiParser factory, got {catalog_pi.parser_factory!r}"
    )


def test_configured_pi_override_propagates_to_dynamic_alias() -> None:
    config = UnifiedConfig(
        agents={
            "pi": AgentConfig(
                cmd="pi-custom",
                transport=AgentTransport.PI,
                session_flag="--session {}",
                yolo_flag="--approve",
            )
        }
    )
    registry = AgentRegistry.from_config(config)

    alias = registry.get("pi/anthropic/claude-sonnet-4-20250514")
    assert alias is not None, (
        "registry.get('pi/anthropic/claude-sonnet-4-20250514') must resolve "
        "through the override base config"
    )
    assert alias.cmd == "pi-custom", (
        f"pi/<model> synthesized config must carry the override cmd, got {alias.cmd!r}"
    )
    assert alias.model_flag == "--model anthropic/claude-sonnet-4-20250514"

    catalog_alias = registry.catalog.get("pi/anthropic/claude-sonnet-4-20250514")
    assert catalog_alias is not None, (
        "registry.catalog.get('pi/anthropic/claude-sonnet-4-20250514') must "
        "resolve through the override base config"
    )
    assert catalog_alias.config.cmd == "pi-custom", (
        f"pi/<model> catalog support config.cmd must be the override, "
        f"got {catalog_alias.config.cmd!r}"
    )
    assert catalog_alias.config.model_flag == "--model anthropic/claude-sonnet-4-20250514"


def test_ccs_dynamic_alias_resolves_on_both_registry_and_catalog() -> None:
    config = UnifiedConfig(
        ccs_aliases={"mm": "ccs mm"},
    )
    registry = AgentRegistry.from_config(config)

    direct = registry.get("ccs/mm")
    assert direct is not None, "registry.get('ccs/mm') must resolve the alias"
    assert direct.cmd == "ccs mm", (
        f"registry.get('ccs/mm').cmd must carry the multi-word "
        f"synthesized command, got {direct.cmd!r}"
    )
    assert direct.transport == AgentTransport.CLAUDE, (
        f"ccs/<alias> aliases must resolve to claude transport, got {direct.transport!r}"
    )

    catalog_support = registry.catalog.get("ccs/mm")
    assert catalog_support is not None, (
        "registry.catalog.get('ccs/mm') must resolve the alias; "
        "without this fix the catalog returns None for multi-word "
        "synthesized cmd values that are absent from _by_command"
    )
    assert catalog_support.config.cmd == "ccs mm", (
        f"registry.catalog.get('ccs/mm').config.cmd must be the "
        f"synthesized multi-word command, got {catalog_support.config.cmd!r}"
    )
    assert catalog_support.config.transport == AgentTransport.CLAUDE, (
        f"registry.catalog.get('ccs/mm').config.transport must be "
        f"claude, got {catalog_support.config.transport!r}"
    )
    assert catalog_support.spec.transport == AgentTransport.CLAUDE, (
        f"registry.catalog.get('ccs/mm').spec.transport must be claude, "
        f"got {catalog_support.spec.transport!r}"
    )
    assert catalog_support.parser_factory is ClaudeParser, (
        f"ccs/<alias> must inherit the claude built-in's parser factory "
        f"(ClaudeParser), got {catalog_support.parser_factory!r}"
    )
