"""Tests proving legacy module dicts are read-only views.

The _PARSER_REGISTRY, _CUSTOM_COMMAND_REGISTRY, and _STRATEGY_DISPATCH
module-level dicts are MappingProxyType (read-only) views over the
default AgentCatalog's state dicts.

These tests verify that the legacy module-level dicts are read-only views
(MappingProxyType) over the AgentCatalog, not mutable write-through buffers.
"""

from __future__ import annotations

import types
from typing import TYPE_CHECKING

import pytest

from ralph.agents.catalog import default_catalog
from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.execution_state._factory import _STRATEGY_DISPATCH
from ralph.agents.parsers import (
    _CUSTOM_COMMAND_REGISTRY,
    _PARSER_REGISTRY,
)
from ralph.agents.parsers._template import ParserTemplateBase
from ralph.agents.parsers.agent_output_line import AgentOutputLine
from ralph.config.enums import AgentTransport

if TYPE_CHECKING:
    from collections.abc import Iterator


class _FakeParser(ParserTemplateBase):
    _STOP_EVENT_TYPES = frozenset()

    def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
        stripped = line.strip()
        result = self.parse_json_line(stripped)
        if result is not None:
            yield result
        else:
            yield AgentOutputLine(type="raw", content=stripped, raw=stripped)


class _FakeStrategy(BaseExecutionStrategy):
    pass


class TestLegacyDictsAreViews:
    """Test that legacy module dicts behave as read-only views."""

    def test_writes_through_to_legacy_dicts_are_now_blocked(self) -> None:
        """Legacy dicts must be MappingProxyType (read-only) instances."""
        assert isinstance(_PARSER_REGISTRY, types.MappingProxyType), (
            "_PARSER_REGISTRY must be MappingProxyType, not dict"
        )
        assert isinstance(_CUSTOM_COMMAND_REGISTRY, types.MappingProxyType), (
            "_CUSTOM_COMMAND_REGISTRY must be MappingProxyType, not dict"
        )
        assert isinstance(_STRATEGY_DISPATCH, types.MappingProxyType), (
            "_STRATEGY_DISPATCH must be MappingProxyType, not dict"
        )

    def test_del_parser_registry_raises_type_error(self) -> None:
        """Deleting from _PARSER_REGISTRY must raise TypeError (read-only)."""
        with pytest.raises(TypeError):
            del _PARSER_REGISTRY["claude"]

    def test_del_custom_command_registry_raises_type_error(self) -> None:
        """Deleting from _CUSTOM_COMMAND_REGISTRY must raise TypeError (read-only)."""
        with pytest.raises(TypeError):
            del _CUSTOM_COMMAND_REGISTRY["some-command"]

    def test_del_strategy_dispatch_raises_type_error(self) -> None:
        """Deleting from _STRATEGY_DISPATCH must raise TypeError (read-only)."""
        with pytest.raises(TypeError):
            del _STRATEGY_DISPATCH[AgentTransport.CLAUDE]

    def test_parser_registry_supports_keyed_lookup(self) -> None:
        """_PARSER_REGISTRY must support [] lookup (MappingProxyType does)."""
        parser = _PARSER_REGISTRY["claude"]()
        assert parser is not None

    def test_custom_command_registry_matches_catalog_state(self) -> None:
        """_CUSTOM_COMMAND_REGISTRY has same length as the default catalog's state."""
        assert len(_CUSTOM_COMMAND_REGISTRY) == len(default_catalog()._state.commands)

    def test_strategy_dispatch_keys_unchanged(self) -> None:
        """_STRATEGY_DISPATCH must contain all AgentTransport values."""
        for transport in AgentTransport:
            assert transport in _STRATEGY_DISPATCH, (
                f"{transport} must be in _STRATEGY_DISPATCH"
            )
