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
from ralph.agents.registration import (
    register_agent_support,
    register_agent_support_to_catalog,
    register_my_agent,
)
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
            "no_default_session_flag",
        }
        assert param_names == expected, (
            f"register_agent_support signature changed. Expected {expected}, got {param_names}"
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


class TestRegistrationDelegationToAgentCatalog:
    """Pin the post-consolidation contract: every public registration
    helper delegates to ``AgentCatalog.add`` (or, for
    ``register_my_agent``, through ``register_agent_support`` which itself
    delegates to ``AgentCatalog.add``).

    These tests use ``monkeypatch`` on the class method so any future
    wrapper or subclass is still detected — only the actual class method
    counts as a call.
    """

    def test_register_agent_support_delegates_to_agent_catalog_add(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``register_agent_support`` calls ``AgentCatalog.add`` exactly once
        with an :class:`AgentSupport` instance.
        """
        captured: list[AgentSupport] = []
        original_add = AgentCatalog.add

        def mock_add(self: AgentCatalog, support: AgentSupport) -> None:
            captured.append(support)
            return original_add(self, support)

        monkeypatch.setattr(AgentCatalog, "add", mock_add)

        register_agent_support(
            name="delegation-shim-agent",
            transport=AgentTransport.GENERIC,
            parser_factory=_FakeParser,
            strategy_factory=_FakeStrategy,
            agent_registry=AgentRegistry(),
            json_parser=JsonParserType.GENERIC,
            interactive=False,
            cmd="delegation-shim-agent",
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

        assert len(captured) == 1, (
            f"register_agent_support must call AgentCatalog.add exactly once, "
            f"got {len(captured)} call(s)"
        )
        captured_support = captured[0]
        assert isinstance(captured_support, AgentSupport)
        assert captured_support.name == "delegation-shim-agent"
        assert captured_support.transport == AgentTransport.GENERIC

    def test_register_agent_support_to_catalog_delegates_to_agent_catalog_add(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``register_agent_support_to_catalog`` calls ``AgentCatalog.add``
        exactly once with the pre-built :class:`AgentSupport` instance.
        """
        captured: list[AgentSupport] = []
        original_add = AgentCatalog.add

        def mock_add(self: AgentCatalog, support: AgentSupport) -> None:
            captured.append(support)
            return original_add(self, support)

        monkeypatch.setattr(AgentCatalog, "add", mock_add)

        name = "delegation-catalog-agent"
        support = AgentSupport.from_registration_kwargs(
            name,
            transport=AgentTransport.GENERIC,
            parser_factory=_FakeParser,
            strategy_factory=_FakeStrategy,
        )
        catalog = AgentCatalog()
        register_agent_support_to_catalog(name, support, catalog)

        assert len(captured) == 1, (
            f"register_agent_support_to_catalog must call AgentCatalog.add exactly once, "
            f"got {len(captured)} call(s)"
        )
        assert captured[0] is support, (
            "register_agent_support_to_catalog must forward the same AgentSupport "
            "instance to AgentCatalog.add"
        )

    def test_register_my_agent_delegates_through_register_agent_support(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``register_my_agent`` routes through ``register_agent_support``
        exactly once with the expected kwargs.
        """
        captured: list[dict[str, object]] = []
        original_register_agent_support = register_agent_support

        def mock_register_agent_support(*args: object, **kwargs: object) -> AgentConfig:
            captured.append({"args": args, "kwargs": kwargs})
            return original_register_agent_support(*args, **kwargs)

        monkeypatch.setattr(
            "ralph.agents.registration.register_agent_support",
            mock_register_agent_support,
        )

        registry = AgentRegistry()
        register_my_agent(
            name="delegation-my-agent",
            transport=AgentTransport.GENERIC,
            parser=_FakeParser,
            agent_registry=registry,
        )

        assert len(captured) == 1, (
            f"register_my_agent must call register_agent_support exactly once, "
            f"got {len(captured)} call(s)"
        )
        call_args = captured[0]["args"]
        call_kwargs = captured[0]["kwargs"]
        assert call_args[0] == "delegation-my-agent", (
            f"register_my_agent must forward name as first positional arg, got {call_args[0]!r}"
        )
        assert call_kwargs["transport"] == AgentTransport.GENERIC
        assert call_kwargs["agent_registry"] is registry
        assert call_kwargs["interactive"] is False
        assert "parser_factory" in call_kwargs
        assert "strategy_factory" in call_kwargs
