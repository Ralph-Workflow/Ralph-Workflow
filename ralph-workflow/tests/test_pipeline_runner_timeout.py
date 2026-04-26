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


def _make_config_with_watchdog(
    agent_idle_timeout_seconds: float = 300.0,
    agent_idle_drain_window_seconds: float = 0.5,
    agent_idle_max_waiting_on_child_seconds: float = 1800.0,
) -> UnifiedConfig:
    return UnifiedConfig(
        general=GeneralConfig(
            agent_idle_timeout_seconds=agent_idle_timeout_seconds,
            agent_idle_drain_window_seconds=agent_idle_drain_window_seconds,
            agent_idle_max_waiting_on_child_seconds=agent_idle_max_waiting_on_child_seconds,
        )
    )


def _capture_options_factory(captured: dict[str, object]) -> object:
    """Return a fake_invoke_agent that captures the full options object."""
    from ralph.config.models import AgentConfig as _AgentConfig  # noqa: PLC0415

    def fake_invoke_agent(
        cfg: _AgentConfig,
        prompt_file: str,
        *,
        options: object = None,
    ) -> list[str]:
        del cfg, prompt_file
        captured["options"] = options
        return []

    return fake_invoke_agent


def _run_with_config(
    config: UnifiedConfig,
    effect: object,
    captured: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Helper that wires up mocks and runs _execute_agent_effect."""

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

    monkeypatch.setattr(runner_module, "start_mcp_server", fake_start_mcp_server)
    monkeypatch.setattr(runner_module, "shutdown_mcp_server", fake_shutdown_mcp_server)
    monkeypatch.setattr(runner_module, "materialize_system_prompt", fake_materialize_system_prompt)

    deps = runner_module._AgentExecutionDeps(
        invoke_agent=_capture_options_factory(captured),
        agent_invocation_error=RuntimeError,
        agent_registry=_registry_factory(config),
    )
    runner_module._execute_agent_effect(effect, config, deps, WorkspaceScope(tmp_path))


def test_config_drain_window_seconds_flows_to_invoke_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """GeneralConfig.agent_idle_drain_window_seconds is passed to InvokeOptions."""
    custom_drain = 2.5
    config = _make_config_with_watchdog(agent_idle_drain_window_seconds=custom_drain)
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md")
    captured: dict[str, object] = {}

    _run_with_config(config, effect, captured, monkeypatch, tmp_path)

    options = captured.get("options")
    assert getattr(options, "drain_window_seconds", None) == custom_drain


def test_config_max_waiting_seconds_flows_to_invoke_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """GeneralConfig.agent_idle_max_waiting_on_child_seconds is passed to InvokeOptions."""
    custom_max = 900.0
    config = _make_config_with_watchdog(agent_idle_max_waiting_on_child_seconds=custom_max)
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md")
    captured: dict[str, object] = {}

    _run_with_config(config, effect, captured, monkeypatch, tmp_path)

    options = captured.get("options")
    assert getattr(options, "max_waiting_on_child_seconds", None) == custom_max


def test_config_default_drain_window_is_0_5() -> None:
    """Default GeneralConfig.agent_idle_drain_window_seconds is 0.5s."""
    config = GeneralConfig()
    assert config.agent_idle_drain_window_seconds == 0.5  # noqa: PLR2004


def test_config_default_max_waiting_is_1800() -> None:
    """Default GeneralConfig.agent_idle_max_waiting_on_child_seconds is 1800.0s."""
    config = GeneralConfig()
    assert config.agent_idle_max_waiting_on_child_seconds == 1800.0  # noqa: PLR2004
