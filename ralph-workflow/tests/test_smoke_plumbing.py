"""Tests for the smoke-test plumbing core.

These tests are black-box and use injected fakes only: no real subprocess,
no real network, no time.sleep, no real file I/O.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke import InvokeOptions
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.factory import PipelineDeps
from ralph.pipeline.plumbing import smoke_plumbing as smoke_plumbing_module
from ralph.pipeline.plumbing.smoke_run_params import SmokeRunParams

if TYPE_CHECKING:
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
    from ralph.pipeline.session_bridge import (
        BuildSessionMcpPlanFn,
        StartMcpServerFn,
        WorkspaceFactoryFn,
    )
    from ralph.policy.models import AgentsPolicy

# Smoke-turn tests stream long synthetic agent output; under full-suite
# worksteal parallelism the default 1s wall-clock alarm intermittently
# fires on a loaded machine even though each test normally finishes fast.
pytestmark = pytest.mark.timeout_seconds(5)


class _FakeBridge:
    def reset_tool_registry(self) -> None:
        return None


def test_resolve_smoke_harness_spec_claude_uses_legacy_layout() -> None:
    spec = smoke_plumbing_module.resolve_smoke_harness_spec("claude/haiku")
    assert spec.relative_dir == Path("tmp/interactive-claude-smoke")
    assert spec.output_file == Path("tmp/interactive-claude-smoke/todo-list.js")
    assert spec.run_id == "interactive-claude-smoke"


def test_resolve_smoke_harness_spec_agy_uses_agy_layout() -> None:
    spec = smoke_plumbing_module.resolve_smoke_harness_spec("agy/gemini-3.5-flash-low")
    assert spec.relative_dir == Path("tmp/interactive-agy-smoke")
    assert spec.output_file == Path("tmp/interactive-agy-smoke/todo-list.js")
    assert spec.run_id == "interactive-agy-smoke-gemini-3.5-flash-low"


def test_run_smoke_plumbing_forwards_agent_name_to_harness_spec(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured_run_ids: list[str] = []

    def fake_execute_agent_effect(*_args: object, **kwargs: object) -> PipelineEvent:
        run_id = kwargs.get("run_id")
        if isinstance(run_id, str):
            captured_run_ids.append(run_id)
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        smoke_plumbing_module,
        "AgentRegistry",
        _make_fake_registry(agent_name="agy/gemini-3.5-flash-low"),
    )
    monkeypatch.setattr(
        smoke_plumbing_module,
        "execute_agent_effect",
        fake_execute_agent_effect,
    )

    output_path = tmp_path / "tmp" / "interactive-agy-smoke" / "todo-list.js"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("export const todos = [];\n", encoding="utf-8")
    artifact_dir = tmp_path / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "smoke_test_result.json").write_text(
        '{"name":"smoke_test_result","artifact_type":"smoke_test_result",'
        '"content":{"status":"passed","summary":"ok"},'
        '"created_at":"now","updated_at":"now","metadata":{}}',
        encoding="utf-8",
    )

    result = smoke_plumbing_module.run_smoke_plumbing(
        config=_fake_config(),
        workspace_root=tmp_path,
        agent_name="agy/gemini-3.5-flash-low",
        prompt_file=tmp_path / "PROMPT.md",
        output_file=output_path,
        display_context=make_display_context(),
        pipeline_deps=PipelineDeps(
            display_context=make_display_context(),
            bridge_factory=_fake_bridge_factory,
        ),
    )

    assert result.agent_name == "agy/gemini-3.5-flash-low"
    assert captured_run_ids == ["interactive-agy-smoke-gemini-3.5-flash-low"]


def _fake_bridge_factory(**_kwargs: object) -> object:
    class FakeBridge:
        def start(self) -> None:
            return None

        def agent_endpoint_uri(self) -> str:
            return "http://127.0.0.1:9999/mcp"

        def endpoint_uri(self) -> str:
            return "http://127.0.0.1:9999/mcp"

        def shutdown(self) -> None:
            return None

    return FakeBridge()


def test_detect_break_indicators_ignores_bypass_status_line() -> None:
    status_line = (
        "\x1b[38;2;255;107;128m⏵⏵ bypass permissions on"
        "\x1b[38;2;153;153;153m (shift+tab to cycle) · ← for agents\x1b[39m"
    )

    assert smoke_plumbing_module._detect_break_indicators([status_line]) == []


def test_detect_break_indicators_flags_prompt_shaped_bypass_warning() -> None:
    warning_prompt = """
    WARNING: Claude Code running in Bypass Permissions mode

    1. No, exit
    2. Yes, I accept

    Enter to confirm · Esc to cancel
    """

    assert smoke_plumbing_module._detect_break_indicators([warning_prompt]) == [
        "unexpected permission prompt observed in transcript"
    ]


def test_detect_smoke_errors_uses_parser_fallback_for_meaningful_output(
    tmp_path: Path,
) -> None:
    output_file = tmp_path / "tmp" / "interactive-claude-smoke" / "todo-list.js"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("export const todos = [];\n", encoding="utf-8")
    artifact_path = tmp_path / ".agent" / "artifacts" / "smoke_test_result.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        '{"name":"smoke_test_result","artifact_type":"smoke_test_result",'
        '"content":{"status":"passed","summary":"ok"},'
        '"created_at":"now","updated_at":"now","metadata":{}}',
        encoding="utf-8",
    )
    config = AgentConfig(
        cmd="claude",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    )
    params = SmokeRunParams(
        agent_name="claude/haiku",
        config=config,
        unified_config=UnifiedConfig(),
        workspace_root=tmp_path,
        prompt_file=Path("PROMPT.md"),
        output_file=output_file,
        options=InvokeOptions(show_progress=False),
        display_context=make_display_context(),
        bridge=None,
    )
    lines = [
        '{"type":"assistant","message":{"type":"message","content":'
        '[{"type":"text","text":"Starting smoke task."}]}}\n',
        '{"type":"assistant","message":{"type":"message","content":'
        '[{"type":"tool_use","name":"mcp__ralph__write_file"}]}}\n',
        '{"type":"assistant","message":{"type":"message","content":'
        '[{"type":"tool_result","content":[{"type":"text","text":"written"}]}]}}\n',
        '{"type":"assistant","message":{"type":"message","content":'
        '[{"type":"text","text":"Submitted artifact."}]}}\n',
    ]

    errors = smoke_plumbing_module._detect_smoke_errors(
        params,
        lines,
        [],
        "sess-1",
        None,
    )

    assert "fewer than 3 meaningful output lines were observed" not in errors
    assert "no tool activity was observed" not in errors


class TestSmokePlumbingCharacterization:
    """Characterization tests pinning smoke plumbing behavior."""

    def test_run_smoke_plumbing_consumes_pipeline_deps_bridge_factory(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        bridge_shutdown: list[bool] = []

        class FakeBridge:
            def start(self) -> None:
                return None

            def agent_endpoint_uri(self) -> str:
                return "http://127.0.0.1:9999/mcp"

            def endpoint_uri(self) -> str:
                return "http://127.0.0.1:9999/mcp"

            def shutdown(self) -> None:
                bridge_shutdown.append(True)

        def fake_bridge_factory(
            *,
            workspace_root: Path,
            drain: str,
            agents_policy: AgentsPolicy | None,
            transport: AgentTransport | None = None,
            capabilities: frozenset[str] | None = None,
            session_id_prefix: str | None = None,
            run_id: str | None = None,
            model_identity: MultimodalModelIdentity | None = None,
            parallel_worker: bool = False,
            build_session_mcp_plan_fn: BuildSessionMcpPlanFn | None = None,
            start_mcp_server_fn: StartMcpServerFn | None = None,
            workspace_factory: WorkspaceFactoryFn | None = None,
        ) -> FakeBridge:
            del workspace_root, drain, agents_policy, transport, capabilities
            del session_id_prefix, run_id, model_identity, parallel_worker
            del build_session_mcp_plan_fn, start_mcp_server_fn, workspace_factory
            return FakeBridge()

        deps = PipelineDeps(
            display_context=make_display_context(),
            bridge_factory=fake_bridge_factory,
        )

        monkeypatch.setattr(
            smoke_plumbing_module,
            "AgentRegistry",
            _make_fake_registry(agent_name="claude/haiku"),
        )

        output_path = tmp_path / "tmp" / "interactive-claude-smoke" / "todo-list.js"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("export const todos = [];\n", encoding="utf-8")
        artifact_dir = tmp_path / ".agent" / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "smoke_test_result.json").write_text(
            '{"name":"smoke_test_result","artifact_type":"smoke_test_result",'
            '"content":{"status":"passed","summary":"ok",'
            '"output_file":"tmp/interactive-claude-smoke/todo-list.js",'
            '"observed_working":["tmp artifact created"],'
            '"observed_breaks":[],'
            '"headless_guide_checks":["session capture"]},'
            '"created_at":"now","updated_at":"now","metadata":{}}',
            encoding="utf-8",
        )

        monkeypatch.setattr(smoke_plumbing_module, "invoke_agent", _fake_invoke_agent)

        result = smoke_plumbing_module.run_smoke_plumbing(
            config=_fake_config(),
            workspace_root=tmp_path,
            agent_name="claude/haiku",
            prompt_file=tmp_path / "PROMPT.md",
            output_file=output_path,
            display_context=make_display_context(),
            pipeline_deps=deps,
        )

        assert result.session_id == "interactive-smoke-session"
        assert bridge_shutdown == [True]


def _make_fake_registry(agent_name: str = "claude/haiku") -> object:
    interactive = AgentConfig(
        cmd="claude",
        output_flag=None,
        yolo_flag="--dangerously-skip-permissions",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    )

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: object) -> FakeRegistry:
            return cls()

        def get(self, name: str) -> AgentConfig | None:
            if name == agent_name:
                return interactive
            return None

    return FakeRegistry


def _fake_invoke_agent(
    _config: AgentConfig,
    _prompt_file: str,
    *,
    options: InvokeOptions | None = None,
) -> object:
    del options
    return iter(
        [
            '{"type":"assistant","message":{"type":"message","content":'
            '[{"type":"text","text":"I am creating the todo list now."}]}\n',
            '{"type":"assistant","message":{"type":"message","content":'
            '[{"type":"text","text":"The file has been written successfully."}]}\n',
            "Claude session ready. Session ID: interactive-smoke-session\n",
            "claude tool: write_file\n",
            "Task declared complete: session_id=interactive-smoke-session, "
            "summary=done, timestamp=1\n",
        ]
    )


def _fake_config() -> UnifiedConfig:
    return UnifiedConfig()
