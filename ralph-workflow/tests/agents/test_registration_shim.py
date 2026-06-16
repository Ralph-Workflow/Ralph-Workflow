"""Tests proving register_agent_support is a thin shim.

These tests verify that register_agent_support:
1. Has an unchanged public signature
2. Returns an AgentConfig that matches what's in the catalog
3. Calls AgentSupport.from_registration_kwargs and AgentCatalog.add in order
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

from ralph.agents.catalog import AgentCatalog, default_catalog
from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.parsers._template import ParserTemplateBase
from ralph.agents.registration import register_agent_support
from ralph.agents.registry import AgentRegistry
from ralph.agents.support import AgentSupport
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport, JsonParserType

if TYPE_CHECKING:
    from collections.abc import Iterator

    import pytest

    from ralph.agents.parsers.agent_output_line import AgentOutputLine


class _FakeParser(ParserTemplateBase):
    _STOP_EVENT_TYPES = frozenset()

    def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
        result = self.parse_json_line(line)
        if result is not None:
            yield result


class _FakeStrategy(BaseExecutionStrategy):
    pass


class TestRegisterAgentSupportShim:
    """Test that register_agent_support is a thin shim."""

    def test_register_agent_support_signature_unchanged(self) -> None:
        """The public signature must not change (additive only)."""
        sig = inspect.signature(register_agent_support)
        param_names = set(sig.parameters.keys())

        expected = {
            "name",
            "transport",
            "parser_factory",
            "strategy_factory",
            "agent_registry",
            "json_parser",
            "interactive",
            "cmd",
            "output_flag",
            "yolo_flag",
            "verbose_flag",
            "can_commit",
            "model_flag",
            "print_flag",
            "streaming_flag",
            "session_flag",
            "display_name",
            "subagent_capability",
        }
        assert param_names == expected, (
            f"register_agent_support signature changed. "
            f"Expected {expected}, got {param_names}"
        )

    def test_register_agent_support_returns_agent_config_from_catalog(self) -> None:
        """register_agent_support returns an AgentConfig matching what's in the catalog."""
        name = "test-shim-agent"

        config = register_agent_support(
            name=name,
            transport=AgentTransport.GENERIC,
            parser_factory=_FakeParser,
            strategy_factory=_FakeStrategy,
            agent_registry=AgentRegistry(),
            json_parser=JsonParserType.GENERIC,
            interactive=False,
            cmd=name,
            output_flag=None,
            yolo_flag=None,
            verbose_flag=None,
            can_commit=False,
            model_flag=None,
            print_flag=None,
            streaming_flag=None,
            session_flag=None,
            display_name=None,
            subagent_capability=None,
        )

        assert isinstance(config, AgentConfig)
        stored = default_catalog().get(name)
        assert stored is not None
        assert stored.config is config
        assert stored.name == name

    def test_register_agent_support_calls_helpers_in_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """register_agent_support delegates to from_registration_kwargs then add."""
        call_order: list[str] = []

        original_method = AgentSupport.from_registration_kwargs

        @classmethod
        def mock_from_kwargs(cls: type[AgentSupport], name: str, **kwargs: object) -> object:
            call_order.append("from_registration_kwargs")
            return original_method(name, **kwargs)

        original_add = AgentCatalog.add

        def mock_add(self: AgentCatalog, support: AgentSupport) -> None:
            call_order.append("add")
            original_add(self, support)

        monkeypatch.setattr(
            AgentSupport,
            "from_registration_kwargs",
            mock_from_kwargs,
        )
        monkeypatch.setattr(AgentCatalog, "add", mock_add)

        register_agent_support(
            name="test-order-agent",
            transport=AgentTransport.GENERIC,
            parser_factory=_FakeParser,
            strategy_factory=_FakeStrategy,
            agent_registry=AgentRegistry(),
            json_parser=JsonParserType.GENERIC,
            interactive=False,
            cmd="test-order-agent",
            output_flag=None,
            yolo_flag=None,
            verbose_flag=None,
            can_commit=False,
            model_flag=None,
            print_flag=None,
            streaming_flag=None,
            session_flag=None,
            display_name=None,
            subagent_capability=None,
        )

        assert call_order == [
            "from_registration_kwargs",
            "add",
        ], f"Expected ['from_registration_kwargs', 'add'], got {call_order}"
