"""Tests for fresh bridge acquisition on McpServerError retry.

R4: When McpServerError is raised, the next attempt in a fresh attempt loop
gets a new bridge_factory(...) call, and the conversion captures the rich
McpServerError fields in retry_intent.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

from ralph.agents.invoke import AgentInvocationError
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.workspace import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps


class _FakeBridge:
    def shutdown(self) -> None:
        """Matches RestartAwareMcpBridge.shutdown(self) signature."""
        pass


def _registry_factory(config: object) -> object:
    del config
    agent_config = AgentConfig(cmd="opencode", output_flag="--json-stream")

    class _Registry:
        def get(self, name: str) -> AgentConfig | None:
            del name
            return agent_config

    return _Registry()


def _make_deps(tmp_path: Path):
    def fake_materialize_master_prompt(
        workspace_root: Path,
        name: str,
        default_product_criteria: str | None = None,
        worker_namespace: Path | None = None,
    ) -> str:
        del workspace_root, name, default_product_criteria, worker_namespace
        return str(tmp_path / "MASTER_PROMPT.md")

    return make_test_pipeline_deps(
        display_context=make_display_context(),
        bridge=_FakeBridge(),
        master_prompt_materializer=fake_materialize_master_prompt,
        registry_factory=_registry_factory,
        check_mcp_bridge_health_fn=lambda _: None,
    )


def test_mcp_server_error_conversion_returns_agent_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When run_with_direct_mcp_recovery raises McpServerError, the conversion
    returns PipelineEvent.AGENT_FAILURE."""

    config = UnifiedConfig(general=GeneralConfig(agent_idle_timeout_seconds=300.0))
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md")
    deps = _make_deps(tmp_path)
    fake_bridge = _FakeBridge()

    def fake_run_with_direct_mcp_recovery(*args: object, **kwargs: object) -> object:
        raise McpServerError(
            "MCP server failed",
            restart_count=3,
            last_stage="registry_built",
            elapsed_s=12.5,
            restart_reason="preflight_timeout",
            handover_step="tools/call",
            probe_duration_s=10.0,
            probe_timeout_s=30.0,
            child_pid=12345,
            psutil_status="terminated",
            failure_kind="transient",
        )

    monkeypatch.setattr(
        effect_executor_module,
        "run_with_direct_mcp_recovery",
        fake_run_with_direct_mcp_recovery,
    )

    result = effect_executor_module.execute_agent_effect(
        effect,
        config,
        deps,
        WorkspaceScope(tmp_path),
        display_context=make_display_context(),
        invoke_agent=lambda *_a, **_kw: [],
        agent_invocation_error=AgentInvocationError,
        bridge=fake_bridge,
    )

    assert result == PipelineEvent.AGENT_FAILURE


def test_mcp_server_error_conversion_captures_rich_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The conversion via _handle_terminal_agent_invocation_error captures the rich
    McpServerError fields in the agent_invocation_error_sink."""

    config = UnifiedConfig(general=GeneralConfig(agent_idle_timeout_seconds=300.0))
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md")
    deps = _make_deps(tmp_path)
    fake_bridge = _FakeBridge()

    captured_exc: list[object] = []

    def capture_invocation_error_sink(exc: object) -> object:
        captured_exc.append(exc)
        return None

    def fake_run_with_direct_mcp_recovery(*args: object, **kwargs: object) -> object:
        raise McpServerError(
            "MCP server failed",
            restart_count=3,
            last_stage="registry_built",
            elapsed_s=12.5,
            restart_reason="preflight_timeout",
            handover_step="tools/call",
            probe_duration_s=10.0,
            probe_timeout_s=30.0,
            child_pid=12345,
            psutil_status="terminated",
            failure_kind="transient",
        )

    monkeypatch.setattr(
        effect_executor_module,
        "run_with_direct_mcp_recovery",
        fake_run_with_direct_mcp_recovery,
    )

    result = effect_executor_module.execute_agent_effect(
        effect,
        config,
        deps,
        WorkspaceScope(tmp_path),
        display_context=make_display_context(),
        invoke_agent=lambda *_a, **_kw: [],
        agent_invocation_error=AgentInvocationError,
        agent_invocation_error_sink=capture_invocation_error_sink,
        bridge=fake_bridge,
    )

    assert result == PipelineEvent.AGENT_FAILURE
    assert len(captured_exc) == 1
    exc = captured_exc[0]
    assert isinstance(exc, McpServerError)
    assert exc.last_stage == "registry_built"
    assert exc.elapsed_s == 12.5
    assert exc.restart_reason == "preflight_timeout"
    assert exc.failure_kind == "transient"
    assert exc.child_pid == 12345
    assert exc.psutil_status == "terminated"
