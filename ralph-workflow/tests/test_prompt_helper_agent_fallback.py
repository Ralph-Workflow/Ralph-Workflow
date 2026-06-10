"""Tests for agent fallback behaviour in run_prompt_helper."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any, cast

import pytest

from ralph.cli.commands.prompt_helper import run_prompt_helper
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.config.prompt_helper_config import PromptHelperConfig

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _session_runtime() -> object:
    return import_module("ralph.session_runtime")


class TestAgentFallback:
    """Tests for agent fallback behaviour in run_prompt_helper."""

    def _stub_runtime(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakeRuntime:
            def __enter__(self) -> _FakeRuntime:
                return self

            def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
                del exc_type, exc, tb

            def invoke_prompt_file(self, *args: object, **kwargs: object) -> Iterator[str]:
                del args, kwargs
                return iter(())

        monkeypatch.setattr(
            cast("Any", _session_runtime()).ManagedAgentSessionRuntime,
            "open",
            classmethod(lambda cls, **kwargs: _FakeRuntime()),
        )
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.read_product_spec_artifact",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(
            "ralph.cli.commands.prompt_helper.Prompt.ask",
            lambda *args, **kwargs: "An idea",
        )

    def test_explicit_nonexistent_agent_raises(
        self,
        workspace_root: Path,
    ) -> None:
        """When explicitly set agent is unavailable and no fallback exists, raises RuntimeError."""
        config = UnifiedConfig(
            prompt_helper=PromptHelperConfig(agent="nonexistent-agent"),
            agents={
                "fallback-agent": AgentConfig(
                    cmd="claude",
                    transport=AgentTransport.CLAUDE_INTERACTIVE,
                )
            },
        )
        with pytest.raises(RuntimeError, match=r"nonexistent-agent.*not available"):
            run_prompt_helper(config, workspace_root)

    def test_raises_when_no_fallback_agent_available(
        self,
        workspace_root: Path,
    ) -> None:
        """When configured agent is missing and no agents are configured, raises RuntimeError."""
        config = UnifiedConfig(
            prompt_helper=PromptHelperConfig(agent="nonexistent-agent"),
            agents={},
        )
        with pytest.raises(RuntimeError, match="no fallback agent is available"):
            run_prompt_helper(config, workspace_root)

    def test_omitted_prompt_helper_section_uses_first_configured_agent(
        self,
        workspace_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When [prompt_helper] is absent and agents are configured, uses first agent."""
        # agent=None signals that [prompt_helper] was absent/not overridden
        config = UnifiedConfig(
            prompt_helper=PromptHelperConfig(agent=None),
            agents={
                "first-agent": AgentConfig(
                    cmd="claude",
                    transport=AgentTransport.CLAUDE_INTERACTIVE,
                ),
                "second-agent": AgentConfig(
                    cmd="opencode",
                    transport=AgentTransport.OPENCODE,
                ),
            },
        )
        self._stub_runtime(monkeypatch)
        # Should not raise — first-agent is used as fallback
        run_prompt_helper(config, workspace_root)
