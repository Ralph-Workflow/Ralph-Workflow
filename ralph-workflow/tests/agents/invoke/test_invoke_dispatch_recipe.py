"""Recipe test: prove swapping a custom CommandBuilder/RuntimeResolver works.

This test demonstrates that the 'one file per layer + one dict entry' property
holds for existing AgentTransport values. It uses pytest's monkeypatch.setitem to
register custom CommandBuilder and RuntimeResolver subclasses for AgentTransport.GENERIC
(no enum change, no private helper, no new module-level state).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.invoke import build_command, resolve_invocation_runtime
from ralph.agents.invoke._command_builders import COMMAND_BUILDERS, CommandBuilder
from ralph.agents.invoke._resolved_invocation_runtime import ResolvedInvocationRuntime
from ralph.agents.invoke._runtime_resolvers import RUNTIME_RESOLVERS, RuntimeResolver
from ralph.agents.invoke._types import _BuildCommandOptions
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    import pytest


class _RecipeCommandBuilder(CommandBuilder):
    """Minimal CommandBuilder subclass for testing the swapping recipe."""

    def build(
        self,
        config: AgentConfig,
        prompt_file: str,
        *,
        options: _BuildCommandOptions,
    ) -> list[str]:
        return ["recipe-agent", "run", prompt_file]


class _RecipeRuntimeResolver(RuntimeResolver):
    """Minimal RuntimeResolver subclass for testing the swapping recipe."""

    def resolve(
        self,
        config: AgentConfig,
        extra_env: dict[str, str] | None,
        workspace_path: Path | None,
        *,
        base_env: Mapping[str, str] | None = None,
        system_prompt_file: str | None = None,
        unsafe_mode: bool = False,
    ) -> ResolvedInvocationRuntime:
        return ResolvedInvocationRuntime(
            agent_env={"RECIPE_TEST": "1"},
            server_env=None,
            mcp_endpoint=None,
        )


class TestInvokeDispatchRecipe:
    """Test that swapping a custom CommandBuilder/RuntimeResolver works."""

    def test_swap_command_builder_via_monkeypatch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Assert custom CommandBuilder is used when registered in COMMAND_BUILDERS."""
        original_builder = COMMAND_BUILDERS[AgentTransport.GENERIC]

        try:
            monkeypatch.setitem(
                COMMAND_BUILDERS,
                AgentTransport.GENERIC,
                _RecipeCommandBuilder,
            )

            config = AgentConfig(cmd="recipe-agent", transport=AgentTransport.GENERIC)
            result = build_command(
                config,
                "/tmp/prompt.txt",
                options=_BuildCommandOptions(),
            )

            assert result == ["recipe-agent", "run", "/tmp/prompt.txt"]
        finally:
            monkeypatch.setitem(
                COMMAND_BUILDERS,
                AgentTransport.GENERIC,
                original_builder,
            )

    def test_swap_runtime_resolver_via_monkeypatch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Assert custom RuntimeResolver is used when registered in RUNTIME_RESOLVERS."""

        original_resolver = RUNTIME_RESOLVERS[AgentTransport.GENERIC]

        try:
            monkeypatch.setitem(
                RUNTIME_RESOLVERS,
                AgentTransport.GENERIC,
                _RecipeRuntimeResolver,
            )

            config = AgentConfig(cmd="recipe-agent", transport=AgentTransport.GENERIC)
            result = resolve_invocation_runtime(
                config,
                extra_env={},
                workspace_path=tmp_path,
            )

            assert result.agent_env == {"RECIPE_TEST": "1"}
            assert result.mcp_endpoint is None
        finally:
            monkeypatch.setitem(
                RUNTIME_RESOLVERS,
                AgentTransport.GENERIC,
                original_resolver,
            )

    def test_original_builder_restored_after_monkeypatch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Assert COMMAND_BUILDERS[AgentTransport.GENERIC] is restored after test."""

        original_builder = COMMAND_BUILDERS[AgentTransport.GENERIC]
        original_resolver = RUNTIME_RESOLVERS[AgentTransport.GENERIC]

        try:
            monkeypatch.setitem(
                COMMAND_BUILDERS,
                AgentTransport.GENERIC,
                _RecipeCommandBuilder,
            )
            monkeypatch.setitem(
                RUNTIME_RESOLVERS,
                AgentTransport.GENERIC,
                _RecipeRuntimeResolver,
            )

            config = AgentConfig(cmd="recipe-agent", transport=AgentTransport.GENERIC)
            result = build_command(
                config,
                "/tmp/prompt.txt",
                options=_BuildCommandOptions(),
            )
            assert result == ["recipe-agent", "run", "/tmp/prompt.txt"]

            runtime_result = resolve_invocation_runtime(
                config,
                extra_env={},
                workspace_path=tmp_path,
            )
            assert runtime_result.agent_env == {"RECIPE_TEST": "1"}

        finally:
            monkeypatch.setitem(
                COMMAND_BUILDERS,
                AgentTransport.GENERIC,
                original_builder,
            )
            monkeypatch.setitem(
                RUNTIME_RESOLVERS,
                AgentTransport.GENERIC,
                original_resolver,
            )

        assert COMMAND_BUILDERS[AgentTransport.GENERIC] is original_builder
        assert RUNTIME_RESOLVERS[AgentTransport.GENERIC] is original_resolver
