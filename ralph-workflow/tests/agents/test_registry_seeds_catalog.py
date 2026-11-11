from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.builtin import builtin_supports
from ralph.agents.catalog import default_catalog
from ralph.agents.execution_state._factory import _STRATEGY_DISPATCH
from ralph.agents.parsers import (
    _CUSTOM_COMMAND_REGISTRY,
    _PARSER_REGISTRY,
)
from ralph.agents.registry import AgentRegistry, builtin_agents
from ralph.config.models import UnifiedConfig

if TYPE_CHECKING:
    from ralph.config.enums import AgentTransport


_GOLDEN_PARSERS: dict[str, object] = dict(_PARSER_REGISTRY)
_GOLDEN_CUSTOM: dict[str, object] = dict(_CUSTOM_COMMAND_REGISTRY)
_GOLDEN_STRATEGIES: dict[AgentTransport, object] = dict(_STRATEGY_DISPATCH)


@pytest.fixture(autouse=True)
def _reset_catalog() -> object:
    cat = default_catalog()
    cat._entries.clear()
    cat._by_command.clear()
    cat._state.parsers.clear()
    cat._state.parsers.update(_GOLDEN_PARSERS)
    cat._state.commands.clear()
    cat._state.commands.update(_GOLDEN_CUSTOM)
    cat._state.strategies.clear()
    cat._state.strategies.update(_GOLDEN_STRATEGIES)
    yield
    cat._entries.clear()
    cat._by_command.clear()
    cat._state.parsers.clear()
    cat._state.parsers.update(_GOLDEN_PARSERS)
    cat._state.commands.clear()
    cat._state.commands.update(_GOLDEN_CUSTOM)
    cat._state.strategies.clear()
    cat._state.strategies.update(_GOLDEN_STRATEGIES)


def test_registry_seeds_catalog() -> None:
    catalog = default_catalog()
    registry = AgentRegistry.from_config(UnifiedConfig())

    claude_support = catalog.get("claude")
    assert claude_support is not None
    assert claude_support.spec.requires_pty is True

    headless_support = catalog.get("claude-headless")
    assert headless_support is not None
    assert headless_support.spec.requires_pty is False

    agy_support = catalog.get("agy")
    assert agy_support is not None
    assert agy_support.spec.requires_pty is True

    builtins = {
        "claude",
        "claude-headless",
        "codex",
        "opencode",
        "nanocoder",
        "agy",
        "pi",
        "cursor",
    }
    catalog_agents = set(catalog.list_agents())
    for name in builtins:
        assert name in catalog_agents
        assert name in registry.agents

    registry2 = AgentRegistry.from_config(UnifiedConfig())
    assert registry2 is not None

    legacy = builtin_agents()
    supports = builtin_supports()
    assert len(legacy) == len(supports)
    for s in supports:
        assert s.name in legacy
        assert legacy[s.name].cmd == s.cmd
