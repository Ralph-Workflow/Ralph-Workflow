"""Regression test for clean log rendering on unexpected errors in effect executor.

Ensures that when an unexpected exception is caught during agent invocation,
the logged error message is clean and does not contain empty formatting braces (`{}`).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

from ralph.agents.invoke import AgentInvocationError
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.workspace import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps


class _FakeBridge:
    def shutdown(self) -> None:
        pass


def _make_config(agent_idle_timeout_seconds: float = 300.0) -> UnifiedConfig:
    return UnifiedConfig(
        general=GeneralConfig(agent_idle_timeout_seconds=agent_idle_timeout_seconds)
    )


def _registry_factory(config: object) -> object:
    del config
    agent_config = AgentConfig(cmd="opencode", output_flag="--json-stream")

    class _Registry:
        def get(self, name: str) -> AgentConfig | None:
            del name
            return agent_config

    return _Registry()


def test_execute_agent_effect_unexpected_error_logs_clean_message_without_curlies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unexpected exception during agent invocation logs a message without '{}'."""
    config = _make_config(300.0)
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md")

    def fake_materialize_master_prompt(
        workspace_root: Path,
        name: str,
        default_product_criteria: str | None = None,
        worker_namespace: Path | None = None,
    ) -> str:
        del workspace_root, name, default_product_criteria, worker_namespace
        return str(tmp_path / "MASTER_PROMPT.md")

    def fake_check_mcp_bridge_health(_bridge: object) -> None:
        raise RuntimeError("unexpected bridge crash")

    deps = make_test_pipeline_deps(
        display_context=make_display_context(),
        bridge=_FakeBridge(),
        master_prompt_materializer=fake_materialize_master_prompt,
        registry_factory=_registry_factory,
        check_mcp_bridge_health_fn=fake_check_mcp_bridge_health,
    )

    exception_calls: list[str] = []

    def _spy_exception(message: object, *args: object) -> None:
        del args
        exception_calls.append(str(message))

    monkeypatch.setattr(effect_executor_module.logger, "exception", _spy_exception)

    result = effect_executor_module.execute_agent_effect(
        effect,
        config,
        deps,
        WorkspaceScope(tmp_path),
        display_context=make_display_context(),
        invoke_agent=lambda *_a, **_kw: [],
        agent_invocation_error=AgentInvocationError,
    )

    assert result == PipelineEvent.AGENT_FAILURE
    assert len(exception_calls) == 1
    assert exception_calls[0] == "Unexpected error during agent invocation"
    assert "{}" not in exception_calls[0]


def test_execute_agent_effect_unexpected_error_renders_clean_loguru_output(
    tmp_path: Path,
) -> None:
    """Loguru sink verifies that the rendered exception message has no '{}'."""
    config = _make_config(300.0)
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md")

    def fake_materialize_master_prompt(
        workspace_root: Path,
        name: str,
        default_product_criteria: str | None = None,
        worker_namespace: Path | None = None,
    ) -> str:
        del workspace_root, name, default_product_criteria, worker_namespace
        return str(tmp_path / "MASTER_PROMPT.md")

    def fake_check_mcp_bridge_health(_bridge: object) -> None:
        raise RuntimeError("unexpected bridge crash")

    deps = make_test_pipeline_deps(
        display_context=make_display_context(),
        bridge=_FakeBridge(),
        master_prompt_materializer=fake_materialize_master_prompt,
        registry_factory=_registry_factory,
        check_mcp_bridge_health_fn=fake_check_mcp_bridge_health,
    )

    logged_messages: list[str] = []
    sink_id = effect_executor_module.logger.add(
        logged_messages.append,
        level="ERROR",
        format="{message}",
    )
    try:
        result = effect_executor_module.execute_agent_effect(
            effect,
            config,
            deps,
            WorkspaceScope(tmp_path),
            display_context=make_display_context(),
            invoke_agent=lambda *_a, **_kw: [],
            agent_invocation_error=AgentInvocationError,
        )
    finally:
        effect_executor_module.logger.remove(sink_id)

    assert result == PipelineEvent.AGENT_FAILURE
    matching = [msg for msg in logged_messages if "Unexpected error during agent invocation" in msg]
    assert len(matching) == 1
    first_line = matching[0].splitlines()[0]
    assert first_line == "Unexpected error during agent invocation"
    assert "{}" not in first_line
