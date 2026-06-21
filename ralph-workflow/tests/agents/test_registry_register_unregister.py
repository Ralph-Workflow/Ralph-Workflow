"""Tests for AgentRegistry register and unregister logic."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

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

# Snapshot the default catalog's state at import time so tests that
# mutate it (e.g. the [agents.pi] override tests below) can restore
# the golden built-in seeds before and after they run.  Without
# this, test_registry_register_unregister.py can leave the default
# catalog with an override installed, which would silently leak
# into other tests in the same pytest session (the catalog is a
# module-level singleton).
_GOLDEN_PARSERS: dict[str, object] = dict(_PARSER_REGISTRY)
_GOLDEN_CUSTOM: dict[str, object] = dict(_CUSTOM_COMMAND_REGISTRY)
_GOLDEN_STRATEGIES: dict[AgentTransport, object] = dict(_STRATEGY_DISPATCH)
_GOLDEN_ENTRIES: dict[str, object] = dict(default_catalog()._entries)
_GOLDEN_BY_COMMAND: dict[str, object] = dict(default_catalog()._by_command)


def _restore_golden_catalog() -> None:
    """Reset the default catalog to its import-time built-in seed state.

    The override tests replace the ``pi`` built-in via
    ``AgentCatalog.replace_builtin``, which mutates ``_entries`` and
    ``_by_command``.  This helper is the inverse of that operation
    so a test that ran ``from_config(agents={'pi': ...})`` cannot
    leak a polluted ``pi-custom`` entry into a sibling test that
    later asserts the built-in is still present.
    """
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
    cat._state.strategies.update(cast("dict", _GOLDEN_STRATEGIES))


@pytest.fixture(autouse=True)
def _reset_default_catalog() -> object:
    """Restore the default catalog before AND after each test in this module.

    Necessary because the override tests use ``AgentRegistry.from_config``
    which writes to the module-level default catalog (a singleton).
    Without this fixture, the override would leak into other tests in
    the same pytest session that assert the built-in ``pi`` is present.
    """
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


def test_pi_dynamic_alias_rejects_multi_slash_model_ids() -> None:
    """Multi-slash ``pi/<model>`` aliases must return ``None``.

    Per https://pi.dev/docs/latest/usage the documented ``--model`` pattern
    is ``provider/id`` (exactly one ``/``) with an optional ``:<thinking>``
    suffix.  Aliases with more than one ``/`` inside the model id (e.g.
    ``pi/provider/model/extra`` or ``pi/anthropic/claude/extra``) are
    undocumented and must NOT resolve, since accepting them would emit
    a silently-misparsed ``--model provider/model/extra`` flag downstream.

    The single-segment bare id form (``pi/sonnet``,
    ``pi/claude-sonnet-4-20250514``) and the documented two-segment
    ``provider/id`` form (``pi/anthropic/claude-sonnet-4-20250514``,
    ``pi/anthropic/claude-sonnet-4-20250514:high``) must still resolve.
    """
    registry = AgentRegistry()

    # Three segments after the ``pi/`` prefix (one ``/`` too many).
    assert registry.get("pi/provider/model/extra") is None
    # Three segments with a real provider and two extra model segments.
    assert registry.get("pi/anthropic/claude/extra") is None
    # Three segments with a thinking suffix still has one slash too many.
    assert registry.get("pi/provider/model/extra:high") is None
    # Four segments (deeply nested multi-slash) must also be rejected.
    assert registry.get("pi/anthropic/claude/foo/bar") is None

    # Sanity: documented shapes still resolve and carry the suffix verbatim.
    bare = registry.get("pi/anthropic/claude-sonnet-4-20250514")
    assert bare is not None
    assert bare.model_flag == "--model anthropic/claude-sonnet-4-20250514"

    bare_id = registry.get("pi/sonnet")
    assert bare_id is not None
    assert bare_id.model_flag == "--model sonnet"

    thinking = registry.get("pi/anthropic/claude-sonnet-4-20250514:high")
    assert thinking is not None
    assert thinking.model_flag == "--model anthropic/claude-sonnet-4-20250514:high"


def test_configured_pi_override_propagates_to_catalog() -> None:
    """A configured ``[agents.pi]`` override must reach the public catalog.

    Per the existing ``AgentRegistry.register`` contract, ``registry.get('pi')``
    returns the configured override.  ``registry.catalog.get('pi')`` must
    also return the override so downstream consumers (catalog.get_parser,
    catalog.get_strategy, _resolve_dynamic_support) all see the configured
    command, not the built-in.

    Regression test for the D92 catalog-sync gap where the override reached
    ``self.agents`` but never ``self.catalog``.
    """
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

    # registry.get must reflect the override (existing behavior).
    direct = registry.get("pi")
    assert direct is not None, "registry.get('pi') must resolve the override"
    assert direct.cmd == "pi-custom", (
        f"registry.get('pi').cmd must be the override ('pi-custom'), "
        f"got {direct.cmd!r}"
    )

    # catalog.get must ALSO reflect the override so the public catalog
    # surface stays in lockstep with registry.get.
    catalog_pi = registry.catalog.get("pi")
    assert catalog_pi is not None, (
        "registry.catalog.get('pi') must resolve the configured override; "
        "without this fix the catalog still reports the built-in"
    )
    assert catalog_pi.config.cmd == "pi-custom", (
        f"registry.catalog.get('pi').config.cmd must be 'pi-custom', "
        f"got {catalog_pi.config.cmd!r}"
    )
    # The override must preserve the built-in's parser factory and strategy
    # factory (they are structural to the pi transport, not user-preference).
    assert catalog_pi.parser_factory is PiParser, (
        f"pi override must keep the built-in's PiParser factory, "
        f"got {catalog_pi.parser_factory!r}"
    )


def test_configured_pi_override_propagates_to_dynamic_alias() -> None:
    """The ``pi/<model>`` dynamic alias must use the configured override base.

    Both ``registry.get('pi/<model>')`` and ``registry.catalog.get('pi/<model>')``
    must derive the synthesized support from the override's cmd/flags, not
    from the built-in.  Without this, a user who points ``[agents.pi]`` at a
    custom binary still gets the built-in's ``pi`` cmd when they ask for
    ``pi/anthropic/claude-sonnet-4-20250514``.

    Regression test for the D92 dynamic-alias-sync gap.
    """
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

    # registry.get path
    alias = registry.get("pi/anthropic/claude-sonnet-4-20250514")
    assert alias is not None, (
        "registry.get('pi/anthropic/claude-sonnet-4-20250514') must resolve "
        "through the override base config"
    )
    assert alias.cmd == "pi-custom", (
        f"pi/<model> synthesized config must carry the override cmd, "
        f"got {alias.cmd!r}"
    )
    assert alias.model_flag == "--model anthropic/claude-sonnet-4-20250514"

    # registry.catalog.get path
    catalog_alias = registry.catalog.get("pi/anthropic/claude-sonnet-4-20250514")
    assert catalog_alias is not None, (
        "registry.catalog.get('pi/anthropic/claude-sonnet-4-20250514') must "
        "resolve through the override base config"
    )
    assert catalog_alias.config.cmd == "pi-custom", (
        f"pi/<model> catalog support config.cmd must be the override, "
        f"got {catalog_alias.config.cmd!r}"
    )
    assert (
        catalog_alias.config.model_flag
        == "--model anthropic/claude-sonnet-4-20250514"
    )


def test_ccs_dynamic_alias_resolves_on_both_registry_and_catalog() -> None:
    """A ``ccs/<alias>`` dynamic alias must resolve on both surfaces.

    Regression test for the D92 follow-up where ``ccs/<alias>``
    synthesized ``AgentConfig.cmd`` is a multi-word string (e.g.
    ``"ccs mm"``) that is NOT registered as a built-in command key.
    The previous implementation looked up the base support by
    ``config.cmd.lower()`` in ``catalog._by_command``, which only
    stores single-token built-in commands; the synthesized multi-word
    cmd was missing from the lookup, so ``registry.catalog.get(...)``
    returned ``None`` while ``registry.get(...)`` returned the
    synthesized config.  The public catalog surface must stay in
    lockstep with the registry surface for every documented dynamic
    alias, including ``ccs/<alias>``.

    Both ``registry.get('ccs/<alias>')`` and
    ``registry.catalog.get('ccs/<alias>')`` must return a synthesized
    support whose ``config.cmd`` carries the multi-word synthesized
    command, ``config.transport`` is the alias's underlying transport
    (here ``claude``), and the parser/strategy factories are the
    built-in's factories for that transport.
    """
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
        f"ccs/<alias> aliases must resolve to claude transport, "
        f"got {direct.transport!r}"
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
