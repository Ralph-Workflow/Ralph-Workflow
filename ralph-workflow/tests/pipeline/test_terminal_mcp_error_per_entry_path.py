"""Tests for the McpServerError -> AGENT_FAILURE conversion path in effect_executor.

C4: Convert typed McpServerError from mcp_supervisor_factory to AgentRetryIntent
BEFORE it re-raises, so the supervisor's typed error surfaces as AGENT_FAILURE
not as an unhandled traceback.
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


def test_mcp_server_error_converts_to_agent_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When run_with_direct_mcp_recovery raises McpServerError, execute_agent_effect
    returns AGENT_FAILURE without an unhandled exception propagating."""

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

    # Pass the bridge so _invoke_agent_with_recovery uses it (own_bridge=False)
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


def test_mcp_server_error_logs_last_stage_at_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """McpServerError with last_stage='registry_built' logs 'last_stage=registry_built' at WARNING level."""

    config = UnifiedConfig(general=GeneralConfig(agent_idle_timeout_seconds=300.0))
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md")
    deps = _make_deps(tmp_path)
    fake_bridge = _FakeBridge()

    logged_warnings: list[str] = []
    sink_id = effect_executor_module.logger.add(
        logged_warnings.append,
        level="WARNING",
        format="{message}",
    )

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

    try:
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
        warning_logs = [m for m in logged_warnings if "last_stage=registry_built" in m]
        assert len(warning_logs) >= 1, (
            f"Expected 'last_stage=registry_built' in WARNING logs, got: {logged_warnings}"
        )
    finally:
        effect_executor_module.logger.remove(sink_id)


def test_phase_handler_does_not_log_mcp_server_error_as_traceback() -> None:
    """After the fix, 'Phase handler crashed' must NOT appear in the source as the
    logger.exception message for McpServerError.

    The fix changes the log message for McpServerError to use logger.warning
    (no traceback) instead of logger.exception (with traceback). We grep for
    logger.exception("Phase handler crashed" to verify the old pattern is gone.
    """
    # Grep for the OLD pattern: logger.exception("Phase handler crashed"
    # This should NOT match the McpServerError branch anymore. Use Path.read_text
    # + str.contains over the ralph/ tree so the static test avoids a real
    # subprocess call (the audit forbids direct subprocess.Popen from tests/).
    root = Path(__file__).parent.parent.parent / "ralph"
    import re

    pattern = re.compile(r'logger\.exception.*"Phase handler crashed')
    matches: list[str] = []
    for path in sorted(root.rglob("*.py")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if pattern.search(text):
            matches.append(str(path))
    assert not matches, f"logger.exception('Phase handler crashed') still appears in: {matches}"
