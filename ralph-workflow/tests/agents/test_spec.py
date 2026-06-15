"""Tests for AgentSpec dataclass."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from ralph.agents.spec import AgentSpec
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport


class TestAgentSpec:
    """Black-box tests for AgentSpec."""

    def test_immutability(self) -> None:
        spec = AgentSpec(name="test", transport=AgentTransport.GENERIC)
        with pytest.raises(FrozenInstanceError):
            del spec.name

    def test_requires_pty_without_interactive_raises(self) -> None:
        with pytest.raises(ValueError, match="requires_pty=True requires"):
            AgentSpec(name="test", transport=AgentTransport.GENERIC, requires_pty=True)

    def test_session_resume_without_completion_raises(self) -> None:
        with pytest.raises(ValueError, match="requires completion_required=True"):
            AgentSpec(
                name="test",
                transport=AgentTransport.GENERIC,
                session_resume_template="--resume {}",
            )

    def test_default_values(self) -> None:
        spec = AgentSpec(name="test", transport=AgentTransport.GENERIC)
        assert spec.interactive is False
        assert spec.requires_pty is False
        assert spec.session_resume_template is None
        assert spec.completion_required is False
        assert spec.subagent_capable is False

    def test_from_agent_config(self) -> None:
        config = AgentConfig(cmd="fake", transport=AgentTransport.GENERIC)
        spec = AgentSpec.from_agent_config(config)
        assert spec.name == "fake"
        assert spec.transport == AgentTransport.GENERIC
        assert spec.interactive is False
        assert spec.requires_pty is False
        assert spec.subagent_capable is False

    def test_from_agent_config_with_interactive(self) -> None:
        config = AgentConfig(
            cmd="claude",
            transport=AgentTransport.CLAUDE_INTERACTIVE,
            session_flag="--resume {}",
        )
        spec = AgentSpec.from_agent_config(config, interactive=True, completion_required=True)
        assert spec.interactive is True
        assert spec.requires_pty is True
        assert spec.session_resume_template == "--resume {}"
        assert spec.completion_required is True
