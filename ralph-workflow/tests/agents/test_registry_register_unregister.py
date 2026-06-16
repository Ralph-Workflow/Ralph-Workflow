"""Tests for AgentRegistry register and unregister logic."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.catalog import default_catalog
from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.parsers._template import ParserTemplateBase
from ralph.agents.parsers.agent_output_line import AgentOutputLine
from ralph.agents.registration import register_agent_support
from ralph.agents.registry import AgentRegistry
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport

if TYPE_CHECKING:
    from collections.abc import Iterator


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
    # (b) registering a headless agent and unregistering it can be repeated 5 times in a row
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
    # (c) the same flow works for an interactive agent
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
    # (d) unregister() on a name in self.agents but NOT in catalog is safe
    # and removes from self.agents only
    registry = AgentRegistry()
    name = "legacy-alias"
    config = AgentConfig(cmd="echo alias", transport=AgentTransport.CLAUDE)
    registry.register(name, config)

    assert name in registry.agents
    # Since we bypassed register_agent_support, it is not in catalog
    assert default_catalog().get(name) is None

    registry.unregister(name)
    assert name not in registry.agents
    assert default_catalog().get(name) is None


def test_pi_dynamic_alias_preserves_provider_id_suffix() -> None:
    """`pi/<model>` dynamic alias must preserve the full provider/id suffix.

    Pi's documented model pattern is `provider/id` (e.g. `anthropic/claude-sonnet-4-20250514`)
    plus an optional `:<thinking>` suffix (e.g. `sonnet:high`).  The dynamic
    alias in ``AgentRegistry._resolve_dynamic_agent`` must use
    ``name.removeprefix('pi/')`` (NOT ``segments[1]``) so the multi-segment
    suffix round-trips intact into the ``--model <pattern>`` flag value.
    """
    registry = AgentRegistry()

    config = registry.get("pi/anthropic/claude-sonnet-4-20250514")
    assert config is not None, "pi/anthropic/claude-sonnet-4-20250514 must resolve"
    assert config.model_flag == "--model anthropic/claude-sonnet-4-20250514"

    config_with_thinking = registry.get("pi/anthropic/claude-sonnet-4-20250514:high")
    assert config_with_thinking is not None
    assert config_with_thinking.model_flag == "--model anthropic/claude-sonnet-4-20250514:high"


def test_pi_dynamic_alias_rejects_malformed_model_ids() -> None:
    """Malformed ``pi/<model>`` aliases must return ``None``.

    Per https://pi.dev/docs/latest/usage the documented --model pattern is
    ``provider/id`` with an optional ``:<thinking>`` suffix.  Aliases with
    empty provider segments, empty model segments, empty thinking suffixes,
    or otherwise structurally invalid shapes must NOT be accepted, since
    they would emit undocumented ``--model <garbage>`` flags downstream.
    """
    registry = AgentRegistry()

    # Empty provider (leading slash with no provider before it).
    assert registry.get("pi//x") is None
    # Empty model id (provider with trailing slash but no model name).
    assert registry.get("pi/provider/") is None
    # Both provider and model id empty.
    assert registry.get("pi//") is None
    # Just the prefix, no model id at all.
    assert registry.get("pi/") is None
    # Trailing slash with no model id (variant of `pi/provider/`).
    assert registry.get("pi/anthropic/") is None
    # Empty thinking suffix after a valid provider/id.
    assert registry.get("pi/anthropic/claude-sonnet-4-20250514:") is None
    # Empty base before the thinking colon.
    assert registry.get("pi/:high") is None
    # Bare colon with nothing on either side.
    assert registry.get("pi/:") is None
