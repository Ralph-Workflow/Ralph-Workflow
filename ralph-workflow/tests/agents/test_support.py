"""Tests for AgentSupport dataclass."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import TYPE_CHECKING

import pytest

from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.spec import AgentSpec
from ralph.agents.support import AgentSupport
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport, JsonParserType

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.parsers.agent_output_line import AgentOutputLine


class _FakeParser:
    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        return iter([])


class _FakeStrategy(BaseExecutionStrategy):
    pass


class TestAgentSupport:
    """Black-box tests for AgentSupport."""

    def test_immutability(self) -> None:
        support = AgentSupport(
            name="test",
            spec=AgentSpec(name="test", transport=AgentTransport.GENERIC),
            parser_factory=_FakeParser,
            strategy_factory=_FakeStrategy,
            config=AgentConfig(cmd="test"),
        )
        with pytest.raises(FrozenInstanceError):
            del support.name

    def test_cmd_property(self) -> None:
        support = AgentSupport(
            name="test",
            spec=AgentSpec(name="test", transport=AgentTransport.GENERIC),
            parser_factory=_FakeParser,
            strategy_factory=_FakeStrategy,
            config=AgentConfig(cmd="my-cmd"),
        )
        assert support.cmd == "my-cmd"

    def test_transport_property(self) -> None:
        support = AgentSupport(
            name="test",
            spec=AgentSpec(name="test", transport=AgentTransport.CLAUDE),
            parser_factory=_FakeParser,
            strategy_factory=_FakeStrategy,
            config=AgentConfig(cmd="test", transport=AgentTransport.CLAUDE),
        )
        assert support.transport == AgentTransport.CLAUDE

    def test_from_registration_kwargs_interactive_defaults(self) -> None:
        support = AgentSupport.from_registration_kwargs(
            "test-agent",
            transport=AgentTransport.CLAUDE_INTERACTIVE,
            parser_factory=_FakeParser,
            strategy_factory=_FakeStrategy,
            interactive=True,
        )
        assert support.spec.interactive is True
        assert support.spec.requires_pty is True
        assert support.spec.session_resume_template == "--resume {}"
        assert support.spec.completion_required is True
        assert support.config.cmd == "test-agent"
        assert support.config.session_flag == "--resume {}"

    def test_from_registration_kwargs_agent_config_roundtrip(self) -> None:
        support = AgentSupport.from_registration_kwargs(
            "my-agent",
            transport=AgentTransport.GENERIC,
            parser_factory=_FakeParser,
            strategy_factory=_FakeStrategy,
            cmd="my-agent",
            output_flag="--output",
            model_flag="claude-4",
        )
        config = support.config
        assert config.cmd == "my-agent"
        assert config.output_flag == "--output"
        assert config.model_flag == "claude-4"

    def test_from_registration_kwargs_legacy_kwargs_compat(self) -> None:
        support = AgentSupport.from_registration_kwargs(
            "legacy-agent",
            transport=AgentTransport.CLAUDE,
            parser_factory=_FakeParser,
            strategy_factory=_FakeStrategy,
            interactive=False,
            cmd="legacy",
            can_commit=True,
            json_parser=JsonParserType.CLAUDE,
            display_name="Legacy Agent",
        )
        assert support.config.can_commit is True
        assert support.config.json_parser == JsonParserType.CLAUDE
        assert support.config.display_name == "Legacy Agent"
        assert support.spec.interactive is False

    def test_name_lower(self) -> None:
        support = AgentSupport(
            name="MyAgent",
            spec=AgentSpec(name="MyAgent", transport=AgentTransport.GENERIC),
            parser_factory=_FakeParser,
            strategy_factory=_FakeStrategy,
            config=AgentConfig(cmd="myagent"),
        )
        assert support._name_lower == "myagent"
