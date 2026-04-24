"""Tests that agent_idle_timeout_seconds from config flows through to InvokeOptions."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.runner import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path


def _make_config(agent_idle_timeout_seconds: float) -> UnifiedConfig:
    return UnifiedConfig(
        general=GeneralConfig(agent_idle_timeout_seconds=agent_idle_timeout_seconds)
    )


def _registry_factory(config: object) -> object:
    agent_config = AgentConfig(cmd="opencode", output_flag="--json-stream")

    class RegistryInstance:
        def get(self, name: str) -> AgentConfig | None:
            del name
            return agent_config

    class Registry:
        @classmethod
        def from_config(cls, cfg: object) -> object:
            del cls, cfg
            return RegistryInstance()

    return Registry


def test_config_idle_timeout_flows_to_invoke_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """UnifiedConfig.general.agent_idle_timeout_seconds is passed to InvokeOptions."""
    custom_timeout = 7.0
    config = _make_config(custom_timeout)
    effect = InvokeAgentEffect(
        agent_name="dev", phase="development", prompt_file="dev.md"
    )
    captured: dict[str, object] = {}

    class FakeBridge:
        def shutdown(self) -> None:
            return

        def agent_endpoint_uri(self) -> str:
            return "http://127.0.0.1:9999/mcp"

    def fake_start_mcp_server(*_args: object, **_kwargs: object) -> FakeBridge:
        return FakeBridge()

    def fake_shutdown_mcp_server(_bridge: object) -> None:
        return

    def fake_materialize_system_prompt(*, workspace_root: Path, name: str) -> str:
        del workspace_root, name
        return str(tmp_path / "SYSTEM_PROMPT.md")

    def fake_invoke_agent(
        cfg: AgentConfig,
        prompt_file: str,
        *,
        options: object = None,
    ) -> list[str]:
        del cfg, prompt_file
        captured["idle_timeout_seconds"] = (
            getattr(options, "idle_timeout_seconds", None) if options else None
        )
        return []

    monkeypatch.setattr(runner_module, "start_mcp_server", fake_start_mcp_server)
    monkeypatch.setattr(runner_module, "shutdown_mcp_server", fake_shutdown_mcp_server)
    monkeypatch.setattr(runner_module, "materialize_system_prompt", fake_materialize_system_prompt)

    deps = runner_module._AgentExecutionDeps(
        invoke_agent=fake_invoke_agent,
        agent_invocation_error=RuntimeError,
        agent_registry=_registry_factory(config),
    )

    runner_module._execute_agent_effect(effect, config, deps, WorkspaceScope(tmp_path))

    assert captured.get("idle_timeout_seconds") == custom_timeout


def test_config_default_idle_timeout_is_300() -> None:
    """Default GeneralConfig.agent_idle_timeout_seconds is 300.0 seconds."""
    config = GeneralConfig()
    assert config.agent_idle_timeout_seconds == 300.0  # noqa: PLR2004


def test_config_idle_timeout_validation_rejects_zero() -> None:
    """agent_idle_timeout_seconds must be greater than zero."""
    with pytest.raises(ValidationError):
        GeneralConfig(agent_idle_timeout_seconds=0.0)


def test_config_idle_timeout_validation_rejects_negative() -> None:
    """agent_idle_timeout_seconds must be greater than zero."""
    with pytest.raises(ValidationError):
        GeneralConfig(agent_idle_timeout_seconds=-1.0)
