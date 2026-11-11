from __future__ import annotations

import os
import re
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

if TYPE_CHECKING:
    from ralph.agents.support import AgentSupport
    from ralph.pipeline.factory import PipelineDeps

from ralph.agents.builtin import builtin_supports
from ralph.agents.invoke import InvokeOptions
from ralph.cli.commands import smoke as smoke_module
from ralph.cli.commands.smoke_binary_override import (
    apply_smoke_binary_override,
    apply_smoke_binary_overrides_to_config,
    resolve_smoke_binary_override,
)
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import DisplayContext
from ralph.display.theme import RALPH_THEME
from ralph.display.vt_normalizer import normalize_vt_text
from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.plumbing import smoke_plumbing as smoke_plumbing_module
from ralph.pipeline.plumbing.smoke_evidence import (
    DEGRADED,
    PASS,
    Evidence,
    Provenance,
    absent,
    grade_verdict,
)
from ralph.pipeline.plumbing.smoke_plumbing import is_mock_agy_override
from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy
from ralph.pro_support.hooks import ProPipelineHooks
from ralph.pro_support.state_query import SnapshotRegistry
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests._support.typed_accessors import (
    must_mapping,
)

pytestmark = pytest.mark.smoke


class _FakePipelineFactory:
    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps
        self.calls: list[dict[str, object]] = []

    def build(
        self,
        config: UnifiedConfig,
        display_context: DisplayContext,
        *,
        model_identity: MultimodalModelIdentity | None = None,
        pro_hooks: ProPipelineHooks | None = None,
        **kwargs: object,
    ) -> PipelineDeps:
        del kwargs
        self.calls.append(
            {
                "config": config,
                "display_context": display_context,
                "model_identity": model_identity,
                "pro_hooks": pro_hooks,
            }
        )
        return self._deps


def _attach_console(monkeypatch: pytest.MonkeyPatch) -> StringIO:
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME)
    ctx = DisplayContext(
        console=console,
        theme=RALPH_THEME,
        width=100,
        mode="default",
        color_enabled=True,
        glyphs_enabled=True,
        headline_max_chars=120,
        condenser_soft_limit=400,
        condenser_hard_limit=4000,
        streaming_checkpoint_chars=4000,
        streaming_checkpoint_fragments=20,
        streaming_dedup_enabled=True,
        streaming_checkpoints_enabled=True,
        thinking_preview_min_chars=80,
        tool_result_headline_min_chars=80,
    )

    def _make_display_context(**_kwargs: object) -> DisplayContext:
        return ctx

    monkeypatch.setattr(smoke_module, "make_display_context", _make_display_context)
    return stream


def test_build_smoke_prompt_targets_tmp_javascript_todo_list() -> None:
    prompt = smoke_module.build_smoke_prompt(
        "tmp/interactive-claude-smoke/todo-list.js",
        submit_artifact_tool_name="mcp__ralph__ralph_submit_md_artifact",
    )

    assert "tmp/interactive-claude-smoke/todo-list.js" in prompt
    assert "JavaScript todo list" in prompt
    assert "declare_complete" in prompt
    assert "smoke_test_result" in prompt
    assert "mcp__ralph__ralph_submit_md_artifact" in prompt
    assert "type: smoke_test_result" in prompt
    assert "status: passed" in prompt
    assert "output_file: tmp/interactive-claude-smoke/todo-list.js" in prompt
    assert "## Observed Working" in prompt
    assert "## Headless Guide Checks" in prompt
    assert "JSON artifact" not in prompt


def test_build_smoke_prompt_teaches_agy_to_route_submission_through_call_mcp_tool() -> None:
    agy_prompt = smoke_module.build_smoke_prompt(
        "tmp/interactive-agy-smoke/todo-list.js",
        submit_artifact_tool_name="ralph_submit_md_artifact",
        transport=AgentTransport.AGY,
    )

    assert "call_mcp_tool" in agy_prompt
    assert "MCP server `ralph`" in agy_prompt
    assert "ralph_submit_md_artifact" in agy_prompt
    assert "target tool `declare_complete`" not in agy_prompt
    assert "call `declare_complete`" in agy_prompt

    non_agy_prompt = smoke_module.build_smoke_prompt(
        "tmp/interactive-claude-smoke/todo-list.js",
        submit_artifact_tool_name="mcp__ralph__ralph_submit_md_artifact",
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    )
    assert "call_mcp_tool" not in non_agy_prompt


def test_render_smoke_report_surfaces_working_and_broken_observations() -> None:
    result = smoke_module.SmokeRunResult(
        agent_name="claude/haiku",
        transport="claude_interactive",
        output_file=Path("tmp/interactive-claude-smoke/todo-list.js"),
        file_created=True,
        session_id="sess-1",
        explicit_completion_seen=absent("test fixture: not observed"),
        raw_line_count=2,
        parsed_event_count=1,
        tool_activity_seen=absent("test fixture: not observed"),
        artifact_submitted=absent("test fixture: not observed"),
        meaningful_output_lines=[
            "thinking: checking prompt",
            "tool_use: write_file",
            "text: wrote file",
        ],
        errors=["missing tool activity"],
    )

    report = smoke_module.render_smoke_report([result])

    assert "Headless semantic guide" in report
    assert "Observed working" in report
    assert "Observed breaks" in report
    assert "missing tool activity" in report
    assert "Observed output" in report
    assert "thinking: checking prompt" in report


def test_smoke_interactive_claude_command_runs_interactive_haiku_and_reports_guided_parity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stream = _attach_console(monkeypatch)
    monkeypatch.delenv("RALPH_BROKER_SECRET", raising=False)
    scope = WorkspaceScope(tmp_path)

    def _resolve_workspace_scope() -> WorkspaceScope:
        return scope

    def _load_config(*_args: object, **_kwargs: object) -> UnifiedConfig:
        return UnifiedConfig(agent_chains={"default": AgentChainConfig(agents=["claude/haiku"])})

    monkeypatch.setattr(smoke_module, "resolve_workspace_scope", _resolve_workspace_scope)
    monkeypatch.setattr(smoke_module, "load_config", _load_config)
    monkeypatch.setattr(
        smoke_module,
        "load_agents_policy_for_workspace_scope",
        lambda *_args, **_kwargs: AgentsPolicy(
            agent_chains={"smoke-development": AgentChainConfig(agents=["claude/haiku"])},
            agent_drains={"development": AgentDrainConfig(chain="smoke-development")},
        ),
    )

    interactive = AgentConfig(
        cmd="claude",
        output_flag=None,
        yolo_flag="--dangerously-skip-permissions",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    )
    requested_agents: list[str] = []
    bridge_shutdown: list[bool] = []

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: UnifiedConfig) -> FakeRegistry:
            return cls()

        def get(self, name: str) -> AgentConfig | None:
            requested_agents.append(name)
            if name == "claude/haiku":
                return interactive
            return None

    class FakeBridge:
        @property
        def run_id(self) -> str:
            return "fake-run-id"

        def start(self) -> None:
            return None

        def agent_endpoint_uri(self) -> str:
            return "http://127.0.0.1:9999/mcp"

        def endpoint_uri(self) -> str:
            return "http://127.0.0.1:9999/mcp"

        def shutdown(self) -> None:
            bridge_shutdown.append(True)

    def fake_execute_agent_effect(
        *_args: object,
        **kwargs: object,
    ) -> object:
        raw_sink = kwargs.get("raw_output_sink")
        rendered_sink = kwargs.get("rendered_output_sink")
        output_path = tmp_path / "tmp" / "interactive-claude-smoke" / "todo-list.js"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("export const todos = [];\n", encoding="utf-8")
        fallback_dir = tmp_path / ".agent" / "tmp"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        (fallback_dir / "smoke_test_result.md").write_text(
            "---\n"
            "type: smoke_test_result\n"
            "status: passed\n"
            "output_file: tmp/interactive-claude-smoke/todo-list.js\n"
            "---\n"
            "\n"
            "## Summary\n"
            "\n"
            "- [SUM-1] ok\n"
            "\n"
            "## Observed Working\n"
            "\n"
            "- [OK-1] tmp artifact created\n"
            "\n"
            "## Headless Guide Checks\n"
            "\n"
            "- [HG-1] session capture\n",
            encoding="utf-8",
        )
        sentinel = tmp_path / ".agent" / "completion_seen_interactive-claude-smoke.json"
        sentinel.write_text(
            '{"run_id": "interactive-claude-smoke"}',
            encoding="utf-8",
        )
        raw_lines = [
            '{"type":"assistant","message":{"type":"message","content":'
            '[{"type":"text","text":"I am creating the todo list now."}]}\n',
            '{"type":"assistant","message":{"type":"message","content":'
            '[{"type":"text","text":"The file has been written successfully."}]}\n',
            "Claude session ready. Session ID: interactive-smoke-session\n",
            "claude tool: write_file\n",
            "Task declared complete: session_id=interactive-smoke-session, "
            "summary=done, timestamp=1\n",
        ]
        rendered_lines = [
            "I am creating the todo list now.",
            "The file has been written successfully.",
            "claude/haiku tool: write_file",
            "Task declared complete",
        ]
        if raw_sink is not None:
            for line in raw_lines:
                raw_sink.append(line)
        if rendered_sink is not None:
            for line in rendered_lines:
                rendered_sink.append(line)
        return PipelineEvent.AGENT_SUCCESS

    def fake_bridge_factory(**_kwargs: object) -> FakeBridge:
        return FakeBridge()

    monkeypatch.setattr(smoke_plumbing_module, "AgentRegistry", FakeRegistry)
    monkeypatch.setattr(smoke_plumbing_module, "execute_agent_effect", fake_execute_agent_effect)

    fake_factory = _FakePipelineFactory(
        make_test_pipeline_deps(
            DisplayContext(
                console=Console(file=StringIO(), force_terminal=False),
                theme=RALPH_THEME,
                width=100,
                mode="default",
                color_enabled=False,
                glyphs_enabled=False,
                headline_max_chars=120,
                condenser_soft_limit=400,
                condenser_hard_limit=4000,
                streaming_checkpoint_chars=4000,
                streaming_checkpoint_fragments=20,
                streaming_dedup_enabled=True,
                streaming_checkpoints_enabled=True,
                thinking_preview_min_chars=80,
                tool_result_headline_min_chars=80,
            ),
            bridge_factory=fake_bridge_factory,
        ),
    )
    monkeypatch.setattr(
        smoke_module,
        "DefaultPipelineFactory",
        lambda *_args, **_kwargs: fake_factory,
    )

    exit_code = smoke_module.smoke_interactive_claude_command(display_context=None)

    assert len(fake_factory.calls) == 1
    assert fake_factory.calls[0]["model_identity"] is None
    assert fake_factory.calls[0]["pro_hooks"] is None

    assert exit_code == 1
    assert bridge_shutdown == [True]
    assert requested_agents == ["claude/haiku"]
    output = stream.getvalue()
    assert "claude/haiku" in output
    assert "Headless semantic guide" in output
    assert "smoke_test_result artifact submitted" in output
    assert "completion sentinel observed" not in output
    assert "Observed output" in output
    assert "I am creating the todo list now." in output
    assert "tool_use: write_file" in output
    assert "write_file" in output
    assert "Observed working" in output
    assert "Observed breaks" in output
    assert "DEGRADED (absent)" in output


def test_smoke_interactive_claude_command_forwards_pro_hooks_and_model_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _attach_console(monkeypatch)
    scope = WorkspaceScope(tmp_path)
    monkeypatch.setattr(smoke_module, "resolve_workspace_scope", lambda: scope)
    monkeypatch.setattr(
        smoke_module,
        "load_config",
        lambda *_a, **_k: UnifiedConfig(
            agent_chains={"default": AgentChainConfig(agents=["claude/haiku"])}
        ),
    )
    monkeypatch.setattr(
        smoke_module,
        "load_agents_policy_for_workspace_scope",
        lambda *_args, **_kwargs: AgentsPolicy(
            agent_chains={"smoke-development": AgentChainConfig(agents=["claude/haiku"])},
            agent_drains={"development": AgentDrainConfig(chain="smoke-development")},
        ),
    )
    monkeypatch.setattr(
        smoke_module,
        "submit_artifact_tool_name_for_transport",
        lambda _transport: "mcp__ralph__ralph_submit_md_artifact",
    )
    monkeypatch.setattr(
        smoke_module,
        "_build_smoke_prompt",
        lambda _output_relpath, **_kwargs: "prompt",
    )

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: UnifiedConfig) -> FakeRegistry:
            return cls()

        def get(self, name: str) -> AgentConfig | None:
            if name == "claude/haiku":
                return AgentConfig(
                    cmd="claude",
                    transport=AgentTransport.CLAUDE_INTERACTIVE,
                )
            return None

    monkeypatch.setattr(smoke_module, "AgentRegistry", FakeRegistry)

    expected_deps = make_test_pipeline_deps(
        DisplayContext(
            console=Console(file=StringIO(), force_terminal=False),
            theme=RALPH_THEME,
            width=100,
            mode="default",
            color_enabled=False,
            glyphs_enabled=False,
            headline_max_chars=120,
            condenser_soft_limit=400,
            condenser_hard_limit=4000,
            streaming_checkpoint_chars=4000,
            streaming_checkpoint_fragments=20,
            streaming_dedup_enabled=True,
            streaming_checkpoints_enabled=True,
            thinking_preview_min_chars=80,
            tool_result_headline_min_chars=80,
        ),
    )
    fake_factory = _FakePipelineFactory(expected_deps)

    captured: dict[str, object] = {}

    def fake_run_smoke_plumbing(**kwargs: object) -> smoke_module.SmokeRunResult:
        captured["plumbing_kwargs"] = kwargs
        return smoke_module.SmokeRunResult(
            agent_name="claude/haiku",
            transport="claude_interactive",
            output_file=tmp_path / "tmp" / "interactive-claude-smoke" / "todo-list.js",
            file_created=True,
            session_id="sess-1",
            explicit_completion_seen=Evidence(True, Provenance.WIRE, "test fixture"),
            raw_line_count=1,
            parsed_event_count=1,
            tool_activity_seen=Evidence(True, Provenance.WIRE, "test fixture"),
            artifact_submitted=Evidence(True, Provenance.WIRE, "test fixture"),
            meaningful_output_lines=["ok"],
            errors=[],
        )

    monkeypatch.setattr(
        smoke_module,
        "DefaultPipelineFactory",
        lambda *_args, **_kwargs: fake_factory,
    )
    monkeypatch.setattr(smoke_module, "run_smoke_plumbing", fake_run_smoke_plumbing)

    pro_hooks = ProPipelineHooks(snapshot_registry=SnapshotRegistry())
    model_identity = MultimodalModelIdentity(provider="claude", model_id="haiku")
    exit_code = smoke_module.smoke_interactive_claude_command(
        display_context=None,
        pro_hooks=pro_hooks,
        model_identity=model_identity,
    )

    assert exit_code == 0
    assert len(fake_factory.calls) == 1
    factory_call = fake_factory.calls[0]
    assert factory_call["model_identity"] is model_identity
    assert factory_call["pro_hooks"] is pro_hooks
    plumbing_kwargs = must_mapping(captured["plumbing_kwargs"])
    assert plumbing_kwargs["pipeline_deps"] is expected_deps


def test_smoke_interactive_agy_command_exits_when_agy_binary_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(smoke_module.shutil, "which", lambda _name: None)

    exit_code = smoke_module.smoke_interactive_agy_command(display_context=None)

    assert exit_code == 2


def test_smoke_interactive_agy_command_exits_when_agent_name_resolves_to_wrong_transport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(smoke_module.shutil, "which", lambda _name: "/usr/bin/agy")
    scope = WorkspaceScope(tmp_path)
    monkeypatch.setattr(smoke_module, "resolve_workspace_scope", lambda: scope)
    monkeypatch.setattr(smoke_module, "load_config", lambda *_a, **_k: UnifiedConfig())

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: UnifiedConfig) -> FakeRegistry:
            return cls()

        def get(self, _name: str) -> AgentConfig | None:
            return AgentConfig(
                cmd="claude",
                transport=AgentTransport.CLAUDE_INTERACTIVE,
            )

    monkeypatch.setattr(smoke_module, "AgentRegistry", FakeRegistry)

    exit_code = smoke_module.smoke_interactive_agy_command(
        agent_name="claude/haiku",
        display_context=None,
    )

    assert exit_code == 2


def test_smoke_interactive_agy_command_runs_agy_harness_when_binary_present_and_transport_matches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stream = _attach_console(monkeypatch)
    monkeypatch.setattr(smoke_module.shutil, "which", lambda _name: "/usr/bin/agy")
    scope = WorkspaceScope(tmp_path)
    monkeypatch.setattr(smoke_module, "resolve_workspace_scope", lambda: scope)
    monkeypatch.setattr(smoke_module, "load_config", lambda *_a, **_k: UnifiedConfig())

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: UnifiedConfig) -> FakeRegistry:
            return cls()

        def get(self, name: str) -> AgentConfig | None:
            if name == "agy/gemini-3.7-flash-medium":
                return AgentConfig(
                    cmd="agy",
                    transport=AgentTransport.AGY,
                )
            return None

    monkeypatch.setattr(smoke_module, "AgentRegistry", FakeRegistry)

    captured: dict[str, object] = {}

    def fake_run_smoke_plumbing(**kwargs: object) -> smoke_module.SmokeRunResult:
        captured["agent_name"] = kwargs["agent_name"]
        return smoke_module.SmokeRunResult(
            agent_name="agy/gemini-3.7-flash-medium",
            transport="agy",
            output_file=tmp_path / "tmp" / "interactive-agy-smoke" / "todo-list.js",
            file_created=True,
            session_id="agy-sess-1",
            explicit_completion_seen=Evidence(True, Provenance.WIRE, "test fixture"),
            raw_line_count=1,
            parsed_event_count=1,
            tool_activity_seen=Evidence(True, Provenance.WIRE, "test fixture"),
            artifact_submitted=Evidence(True, Provenance.WIRE, "test fixture"),
            meaningful_output_lines=["ok"],
            errors=[],
        )

    monkeypatch.setattr(smoke_module, "run_smoke_plumbing", fake_run_smoke_plumbing)

    exit_code = smoke_module.smoke_interactive_agy_command(
        agent_name="agy/gemini-3.7-flash-medium",
        display_context=None,
    )

    assert exit_code == 0
    assert captured["agent_name"] == "agy/gemini-3.7-flash-medium"
    output = stream.getvalue()
    assert "agy/gemini-3.7-flash-medium" in output
    assert "agy/gemini-3.7-flash-medium parity smoke test" in output
    assert "agy/gemini-3.7-flash-medium parity smoke report" in output


def test_smoke_interactive_nanocoder_command_runs_nanocoder_harness_when_binary_present(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stream = _attach_console(monkeypatch)
    monkeypatch.setattr(smoke_module.shutil, "which", lambda _name: "/usr/bin/nanocoder")
    scope = WorkspaceScope(tmp_path)
    monkeypatch.setattr(smoke_module, "resolve_workspace_scope", lambda: scope)
    monkeypatch.setattr(smoke_module, "load_config", lambda *_a, **_k: UnifiedConfig())

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: UnifiedConfig) -> FakeRegistry:
            return cls()

        def get(self, name: str) -> AgentConfig | None:
            if name == "nanocoder":
                return AgentConfig(
                    cmd="nanocoder",
                    transport=AgentTransport.NANOCODER,
                )
            return None

    monkeypatch.setattr(smoke_module, "AgentRegistry", FakeRegistry)

    captured: dict[str, object] = {}

    def fake_run_smoke_plumbing(**kwargs: object) -> smoke_module.SmokeRunResult:
        captured["agent_name"] = kwargs["agent_name"]
        return smoke_module.SmokeRunResult(
            agent_name="nanocoder",
            transport="nanocoder",
            output_file=tmp_path / "tmp" / "interactive-nanocoder-smoke" / "todo-list.js",
            file_created=True,
            session_id=None,
            explicit_completion_seen=Evidence(True, Provenance.WIRE, "test fixture"),
            raw_line_count=1,
            parsed_event_count=1,
            tool_activity_seen=Evidence(True, Provenance.WIRE, "test fixture"),
            artifact_submitted=Evidence(True, Provenance.WIRE, "test fixture"),
            meaningful_output_lines=["ok"],
            errors=[],
        )

    monkeypatch.setattr(smoke_module, "run_smoke_plumbing", fake_run_smoke_plumbing)

    exit_code = smoke_module.smoke_interactive_nanocoder_command(display_context=None)

    assert exit_code == 0
    assert captured["agent_name"] == "nanocoder"
    output = stream.getvalue()
    assert "nanocoder parity smoke test" in output
    assert "nanocoder parity smoke report" in output


def test_smoke_interactive_nanocoder_command_accepts_agent_alias(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stream = _attach_console(monkeypatch)
    monkeypatch.setattr(smoke_module.shutil, "which", lambda _name: "/usr/bin/nanocoder")
    scope = WorkspaceScope(tmp_path)
    monkeypatch.setattr(smoke_module, "resolve_workspace_scope", lambda: scope)
    monkeypatch.setattr(smoke_module, "load_config", lambda *_a, **_k: UnifiedConfig())

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: UnifiedConfig) -> FakeRegistry:
            return cls()

        def get(self, name: str) -> AgentConfig | None:
            if name == "nanocoder/minimax/MiniMax-M3":
                return AgentConfig(
                    cmd="nanocoder",
                    transport=AgentTransport.NANOCODER,
                    model_flag="--provider minimax --model MiniMax-M3",
                )
            return None

    monkeypatch.setattr(smoke_module, "AgentRegistry", FakeRegistry)

    captured: dict[str, object] = {}

    def fake_run_smoke_plumbing(**kwargs: object) -> smoke_module.SmokeRunResult:
        captured["agent_name"] = kwargs["agent_name"]
        return smoke_module.SmokeRunResult(
            agent_name="nanocoder/minimax/MiniMax-M3",
            transport="nanocoder",
            output_file=tmp_path / "tmp" / "interactive-nanocoder-smoke" / "todo-list.js",
            file_created=True,
            session_id=None,
            explicit_completion_seen=Evidence(True, Provenance.WIRE, "test fixture"),
            raw_line_count=1,
            parsed_event_count=1,
            tool_activity_seen=Evidence(True, Provenance.WIRE, "test fixture"),
            artifact_submitted=Evidence(True, Provenance.WIRE, "test fixture"),
            meaningful_output_lines=["ok"],
            errors=[],
        )

    monkeypatch.setattr(smoke_module, "run_smoke_plumbing", fake_run_smoke_plumbing)

    exit_code = smoke_module.smoke_interactive_nanocoder_command(
        agent_name="nanocoder/minimax/MiniMax-M3",
        display_context=None,
    )

    assert exit_code == 0
    assert captured["agent_name"] == "nanocoder/minimax/MiniMax-M3"
    output = stream.getvalue()
    assert "nanocoder/minimax/MiniMax-M3 parity smoke test" in output


def test_smoke_interactive_nanocoder_command_exits_when_binary_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(smoke_module.shutil, "which", lambda _name: None)

    exit_code = smoke_module.smoke_interactive_nanocoder_command(display_context=None)

    assert exit_code == 2


def test_smoke_interactive_nanocoder_command_exits_when_transport_is_wrong(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(smoke_module.shutil, "which", lambda _name: "/usr/bin/nanocoder")
    scope = WorkspaceScope(tmp_path)
    monkeypatch.setattr(smoke_module, "resolve_workspace_scope", lambda: scope)
    monkeypatch.setattr(smoke_module, "load_config", lambda *_a, **_k: UnifiedConfig())

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: UnifiedConfig) -> FakeRegistry:
            return cls()

        def get(self, _name: str) -> AgentConfig | None:
            return AgentConfig(
                cmd="claude",
                transport=AgentTransport.CLAUDE_INTERACTIVE,
            )

    monkeypatch.setattr(smoke_module, "AgentRegistry", FakeRegistry)

    exit_code = smoke_module.smoke_interactive_nanocoder_command(display_context=None)

    assert exit_code == 2


def _nanocoder_support() -> AgentSupport:
    return next(
        support for support in builtin_supports() if support.transport == AgentTransport.NANOCODER
    )


def test_nanocoder_smoke_errors_do_not_require_session_id(tmp_path: Path) -> None:
    output_file = tmp_path / "tmp" / "interactive-nanocoder-smoke" / "todo-list.js"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("export const todos = [];\n", encoding="utf-8")
    config = AgentConfig(cmd="nanocoder", transport=AgentTransport.NANOCODER)
    params = smoke_module.SmokeRunParams(
        agent_name="nanocoder",
        config=config,
        unified_config=UnifiedConfig(),
        workspace_root=tmp_path,
        prompt_file=tmp_path / "tmp" / "interactive-nanocoder-smoke" / "PROMPT.md",
        output_file=output_file,
        options=InvokeOptions(),
        display_context=None,
        pipeline_deps=None,
        bridge=None,
    )

    errors = smoke_plumbing_module._detect_smoke_errors(
        params,
        lines=[
            "[plain] text: writing file",
            "[plain] tool: write_file",
            "[plain] text: done",
            "Task declared complete: session_id=nanocoder-smoke, summary=done, timestamp=1",
        ],
        live_output_lines=["writing file", "tool use", "done"],
        session_id=None,
        final_exception=None,
        tool_activity_seen=True,
        artifact_submitted=True,
        support=_nanocoder_support(),
    )

    assert "session ID was not observed" not in errors


@pytest.mark.timeout_seconds(10)
def test_smoke_interactive_agy_with_mock_binary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stream = _attach_console(monkeypatch)
    scope = WorkspaceScope(tmp_path)
    monkeypatch.setattr(smoke_module, "resolve_workspace_scope", lambda: scope)
    monkeypatch.setattr(smoke_module, "load_config", lambda *_a, **_k: UnifiedConfig())

    mock_agy = Path(__file__).resolve().parent / "_support" / "mock_agy.sh"
    monkeypatch.setenv("RALPH_AGY_BINARY", str(mock_agy))
    monkeypatch.setenv("MOCK_AGY_ARTIFACT_DIR", str(tmp_path))

    exit_code = smoke_module.smoke_interactive_agy_command(
        agent_name="agy/gemini-3.7-flash-medium",
        display_context=None,
    )

    assert exit_code == 1
    output = stream.getvalue()
    assert "agy/gemini-3.7-flash-medium" in output
    assert re.search(r"│\s*agy\s*│\s*yes\s*│", output) is not None


@pytest.mark.timeout_seconds(10)
def test_resolve_agy_binary_override_normalizes_relative_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    mock_source = Path(__file__).resolve().parent / "_support" / "mock_agy.sh"
    relative_target = tmp_path / "tests" / "_support" / "mock_agy.sh"
    relative_target.parent.mkdir(parents=True, exist_ok=True)
    relative_target.write_text(mock_source.read_text(encoding="utf-8"), encoding="utf-8")
    relative_target.chmod(0o755)
    monkeypatch.setenv("RALPH_AGY_BINARY", "tests/_support/mock_agy.sh")
    monkeypatch.chdir(tmp_path)

    resolved = resolve_smoke_binary_override(AgentTransport.AGY)
    assert resolved is not None
    assert Path(resolved).is_absolute()
    assert Path(resolved).resolve() == relative_target.resolve()


@pytest.mark.timeout_seconds(10)
def test_resolve_agy_binary_override_returns_none_for_missing_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RALPH_AGY_BINARY", "/nonexistent/path/to/agy-mock.sh")
    resolved = resolve_smoke_binary_override(AgentTransport.AGY)
    assert resolved is None


@pytest.mark.timeout_seconds(15)
def test_smoke_interactive_agy_with_relative_mock_binary_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stream = _attach_console(monkeypatch)
    scope = WorkspaceScope(tmp_path)
    monkeypatch.setattr(smoke_module, "resolve_workspace_scope", lambda: scope)
    monkeypatch.setattr(smoke_module, "load_config", lambda *_a, **_k: UnifiedConfig())

    mock_source = Path(__file__).resolve().parent / "_support" / "mock_agy.sh"
    mock_module_source = Path(__file__).resolve().parent / "_support" / "mock_agy.py"
    relative_target = tmp_path / "tests" / "_support" / "mock_agy.sh"
    relative_target.parent.mkdir(parents=True, exist_ok=True)
    relative_target.write_text(mock_source.read_text(encoding="utf-8"), encoding="utf-8")
    relative_target.chmod(0o755)
    (tmp_path / "tests" / "__init__.py").write_text("", encoding="utf-8")
    (relative_target.parent / "mock_agy.py").write_text(
        mock_module_source.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setenv("RALPH_AGY_BINARY", "tests/_support/mock_agy.sh")
    monkeypatch.setenv("MOCK_AGY_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    exit_code = smoke_module.smoke_interactive_agy_command(
        agent_name="agy/gemini-3.7-flash-medium",
        display_context=None,
    )

    output = stream.getvalue()
    assert exit_code == 1, (
        f"Expected exit_code 1 (DEGRADED, mock evidence never reaches WIRE) for "
        f"relative mock path; got {exit_code}. Output tail:\n{output[-3000:]}"
    )
    assert re.search(r"\u2502\s*agy\s*\u2502\s*yes\s*\u2502", output) is not None, (
        f"Expected 'file=yes' in parity table; output tail:\n{output[-3000:]}"
    )
    assert "DEGRADED (" in output, (
        f"Expected a named DEGRADED demotion in parity report; output tail:\n{output[-3000:]}"
    )


def test_maybe_apply_agy_binary_override_ignores_nonexecutable_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agy_config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)
    monkeypatch.setenv("RALPH_AGY_BINARY", "/etc/hosts")
    result = apply_smoke_binary_override(agy_config)
    assert result.cmd == "agy"


def test_maybe_apply_agy_binary_override_accepts_mock_shell_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_path = Path(__file__).resolve().parent / "_support" / "mock_agy.sh"
    agy_config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)
    monkeypatch.setenv("RALPH_AGY_BINARY", str(mock_path))
    result = apply_smoke_binary_override(agy_config)
    assert result.cmd != "agy"
    assert str(mock_path) in result.cmd


def test_apply_agy_binary_override_to_config_ignores_nonexecutable_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = UnifiedConfig(
        agents={
            "agy/gemini-3.7-flash-medium": AgentConfig(cmd="agy", transport=AgentTransport.AGY),
            "claude/haiku": AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE),
        }
    )
    monkeypatch.setenv("RALPH_AGY_BINARY", "/etc/hosts")
    result = apply_smoke_binary_overrides_to_config(config)
    assert result.agents["agy/gemini-3.7-flash-medium"].cmd == "agy"
    assert result.agents["claude/haiku"].cmd == "claude"


def test_apply_agy_binary_override_to_config_accepts_mock_shell_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_path = Path(__file__).resolve().parent / "_support" / "mock_agy.sh"
    config = UnifiedConfig(
        agents={
            "agy/gemini-3.7-flash-medium": AgentConfig(cmd="agy", transport=AgentTransport.AGY),
        }
    )
    monkeypatch.setenv("RALPH_AGY_BINARY", str(mock_path))
    result = apply_smoke_binary_overrides_to_config(config)
    agy_cmd = result.agents["agy/gemini-3.7-flash-medium"].cmd
    assert agy_cmd != "agy"
    assert str(mock_path) in agy_cmd


@pytest.mark.timeout_seconds(10)
def test_is_mock_agy_override_classifies_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_path = str(Path(__file__).resolve().parent / "_support" / "mock_agy.sh")
    monkeypatch.setenv("RALPH_AGY_BINARY", mock_path)
    assert is_mock_agy_override() is True

    monkeypatch.setenv("RALPH_AGY_BINARY", "/opt/agy-wrapper/agy")
    assert is_mock_agy_override() is False

    monkeypatch.setenv("RALPH_AGY_BINARY", "agy")
    assert is_mock_agy_override() is False

    monkeypatch.delenv("RALPH_AGY_BINARY", raising=False)
    assert is_mock_agy_override() is False


@pytest.mark.timeout_seconds(10)
def test_maybe_apply_agy_binary_override_accepts_non_mock_executable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stub_path = tmp_path / "agy-wrapper.sh"
    stub_path.write_text("#!/usr/bin/env sh\necho stub\n", encoding="utf-8")
    stub_path.chmod(0o755)

    agy_config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)
    monkeypatch.setenv("RALPH_AGY_BINARY", str(stub_path))
    result = apply_smoke_binary_override(agy_config)
    assert result.cmd != "agy"
    assert str(stub_path) in result.cmd
    assert is_mock_agy_override() is False


@pytest.mark.timeout_seconds(10)
def test_apply_agy_binary_override_to_config_accepts_non_mock_executable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stub_path = tmp_path / "agy-real-wrapper.sh"
    stub_path.write_text("#!/usr/bin/env sh\necho real-wrapper\n", encoding="utf-8")
    stub_path.chmod(0o755)

    config = UnifiedConfig(
        agents={
            "agy/gemini-3.7-flash-medium": AgentConfig(cmd="agy", transport=AgentTransport.AGY),
            "claude/haiku": AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE),
        }
    )
    monkeypatch.setenv("RALPH_AGY_BINARY", str(stub_path))
    result = apply_smoke_binary_overrides_to_config(config)
    agy_cmd = result.agents["agy/gemini-3.7-flash-medium"].cmd
    assert agy_cmd != "agy"
    assert str(stub_path) in agy_cmd
    assert result.agents["claude/haiku"].cmd == "claude"


@pytest.mark.timeout_seconds(10)
def test_smoke_command_exit_code_and_breaks_cell_reflect_evidence_verdict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stream = StringIO()
    console = Console(
        file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME, width=300
    )
    display_context = DisplayContext(
        console=console,
        theme=RALPH_THEME,
        width=300,
        mode="default",
        color_enabled=True,
        glyphs_enabled=True,
        headline_max_chars=120,
        condenser_soft_limit=400,
        condenser_hard_limit=4000,
        streaming_checkpoint_chars=4000,
        streaming_checkpoint_fragments=20,
        streaming_dedup_enabled=True,
        streaming_checkpoints_enabled=True,
        thinking_preview_min_chars=80,
        tool_result_headline_min_chars=80,
    )

    result = smoke_module.SmokeRunResult(
        agent_name="agy/gemini-3.6-flash-low",
        transport="agy",
        output_file=tmp_path / "todo-list.js",
        file_created=True,
        session_id="2f50d6ef-a009-427f-99e8-c58ac99c1f8d",
        raw_line_count=16,
        parsed_event_count=19,
        tool_activity_seen=Evidence(True, Provenance.TRANSCRIPT, "14 frames"),
        artifact_submitted=Evidence(True, Provenance.WORKSPACE_EFFECT, "promoted fallback"),
        explicit_completion_seen=Evidence(True, Provenance.HOST_SYNTHESIZED, "written by harness"),
        meaningful_output_lines=[],
        errors=[],
    )

    verdict_label, _ = grade_verdict(smoke_module._required_evidence(result))
    assert verdict_label == DEGRADED

    exit_code = 0 if not result.errors and verdict_label == PASS else 1
    assert exit_code == 1

    smoke_module._render_smoke_table(
        [result], display_context=display_context, agent_name="agy/gemini-3.6-flash-low"
    )
    rendered = normalize_vt_text(stream.getvalue())
    assert "degraded verdict" in rendered
    assert "DEGRADED (host-synthesized)" in rendered


@pytest.mark.timeout_seconds(10)
def test_render_smoke_table_ungraded_multimodal_cell_is_not_not_requested(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stream = StringIO()
    console = Console(
        file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME, width=300
    )
    display_context = DisplayContext(
        console=console,
        theme=RALPH_THEME,
        width=300,
        mode="default",
        color_enabled=True,
        glyphs_enabled=True,
        headline_max_chars=120,
        condenser_soft_limit=400,
        condenser_hard_limit=4000,
        streaming_checkpoint_chars=4000,
        streaming_checkpoint_fragments=20,
        streaming_dedup_enabled=True,
        streaming_checkpoints_enabled=True,
        thinking_preview_min_chars=80,
        tool_result_headline_min_chars=80,
    )

    result = smoke_module.SmokeRunResult(
        agent_name="agy/gemini-3.6-flash-low",
        transport="agy",
        output_file=tmp_path / "todo-list.js",
        file_created=True,
        session_id="2f50d6ef-a009-427f-99e8-c58ac99c1f8d",
        raw_line_count=16,
        parsed_event_count=19,
        tool_activity_seen=Evidence(True, Provenance.WIRE, "tools/call ledger match"),
        artifact_submitted=Evidence(
            True, Provenance.WIRE, "receipt matched a tools/call ledger record"
        ),
        explicit_completion_seen=Evidence(True, Provenance.WIRE, "declare_complete wire match"),
        meaningful_output_lines=[],
        errors=[],
        multimodal_requested=True,
        multimodal_tool_used=None,
    )

    smoke_module._render_smoke_table(
        [result], display_context=display_context, agent_name="agy/gemini-3.6-flash-low"
    )
    rendered = normalize_vt_text(stream.getvalue())

    assert rendered.count("not requested") == 1, (
        "the Multimodal cell must NOT render 'not requested' for an ungraded "
        "multimodal run; the Subagent column's legitimate 'not requested' "
        "should be the only occurrence"
    )
    assert "DEGRADED (absent)" in rendered


@pytest.mark.timeout_seconds(10)
def test_subagent_dispatch_missing_is_fatal_exit_code() -> None:
    from ralph.pipeline.plumbing.smoke_evidence import (
        DEGRADED,
        PASS,
        Evidence,
        Provenance,
    )

    result = smoke_module.SmokeRunResult(
        agent_name="claude/haiku",
        transport="claude_interactive",
        output_file=Path("tmp/interactive-claude-smoke/todo-list.js"),
        file_created=True,
        session_id="sess-1",
        explicit_completion_seen=Evidence(True, Provenance.WIRE, "test fixture"),
        raw_line_count=10,
        parsed_event_count=12,
        tool_activity_seen=Evidence(True, Provenance.WIRE, "test fixture"),
        artifact_submitted=Evidence(True, Provenance.WIRE, "test fixture"),
        meaningful_output_lines=["ok"],
        errors=["not every subagent dispatch has a correlated result"],
    )
    verdict_label, _ = grade_verdict(smoke_module._required_evidence(result))
    assert verdict_label in {PASS, DEGRADED}
    exit_code = 0 if not result.errors and verdict_label == PASS else 1
    assert exit_code == 1, (
        "subagent dispatch missing correlated result must yield"
        " a non-zero exit; the prior fatal_subagent_errors"
        " allowlist regression silently downgraded this case to"
        " exit 0 (DA-002)"
    )


@pytest.mark.timeout_seconds(10)
def test_subagent_no_post_result_activity_is_fatal_exit_code() -> None:
    from ralph.pipeline.plumbing.smoke_evidence import (
        DEGRADED,
        PASS,
        Evidence,
        Provenance,
    )

    result = smoke_module.SmokeRunResult(
        agent_name="claude-headless/haiku",
        transport="claude_headless",
        output_file=Path("tmp/headless-claude-smoke/todo-list.js"),
        file_created=True,
        session_id="sess-2",
        explicit_completion_seen=Evidence(True, Provenance.WIRE, "test fixture"),
        raw_line_count=10,
        parsed_event_count=12,
        tool_activity_seen=Evidence(True, Provenance.WIRE, "test fixture"),
        artifact_submitted=Evidence(True, Provenance.WIRE, "test fixture"),
        meaningful_output_lines=["ok"],
        errors=["no meaningful activity was observed after the subagent result"],
    )
    verdict_label, _ = grade_verdict(smoke_module._required_evidence(result))
    assert verdict_label in {PASS, DEGRADED}
    exit_code = 0 if not result.errors and verdict_label == PASS else 1
    assert exit_code == 1, (
        "subagent no post-result activity must yield a non-zero"
        " exit; the prior fatal_subagent_errors allowlist"
        " regression silently downgraded this case to exit 0"
        " (DA-002)"
    )


@pytest.mark.timeout_seconds(10)
def test_subagent_dispatch_uncorrelated_returns_nonzero_for_both_transports() -> None:
    from ralph.pipeline.plumbing.smoke_evidence import (
        DEGRADED,
        PASS,
        Evidence,
        Provenance,
    )

    for transport_label in ("claude_interactive", "claude_headless"):
        result = smoke_module.SmokeRunResult(
            agent_name=f"claude/{transport_label}",
            transport=transport_label,
            output_file=Path(f"tmp/{transport_label}-smoke/todo-list.js"),
            file_created=True,
            session_id="sess-x",
            explicit_completion_seen=Evidence(True, Provenance.WIRE, "test fixture"),
            raw_line_count=10,
            parsed_event_count=12,
            tool_activity_seen=Evidence(True, Provenance.WIRE, "test fixture"),
            artifact_submitted=Evidence(True, Provenance.WIRE, "test fixture"),
            meaningful_output_lines=["ok"],
            errors=["not every subagent dispatch has a correlated result"],
        )
        verdict_label, _ = grade_verdict(smoke_module._required_evidence(result))
        assert verdict_label in {PASS, DEGRADED}
        exit_code = 0 if not result.errors and verdict_label == PASS else 1
        assert exit_code == 1, (
            f"{transport_label} subagent contract failure must yield a non-zero exit (DA-002)"
        )


@pytest.mark.timeout_seconds(10)
def test_no_subagent_errors_with_pass_verdict_yields_zero_exit() -> None:
    from ralph.pipeline.plumbing.smoke_evidence import (
        PASS,
        Evidence,
        Provenance,
    )

    result = smoke_module.SmokeRunResult(
        agent_name="claude/haiku",
        transport="claude_interactive",
        output_file=Path("tmp/interactive-claude-smoke/todo-list.js"),
        file_created=True,
        session_id="sess-clean",
        explicit_completion_seen=Evidence(True, Provenance.WIRE, "test fixture"),
        raw_line_count=10,
        parsed_event_count=12,
        tool_activity_seen=Evidence(True, Provenance.WIRE, "test fixture"),
        artifact_submitted=Evidence(True, Provenance.WIRE, "test fixture"),
        meaningful_output_lines=["ok"],
        errors=[],
    )
    verdict_label, _ = grade_verdict(smoke_module._required_evidence(result))
    assert verdict_label == PASS
    exit_code = 0 if not result.errors and verdict_label == PASS else 1
    assert exit_code == 0


@pytest.mark.timeout_seconds(10)
def test_smoke_harness_agent_command_mints_run_scoped_broker_secret_when_unset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stream = _attach_console(monkeypatch)
    del stream
    scope = WorkspaceScope(tmp_path)
    monkeypatch.setattr(smoke_module, "resolve_workspace_scope", lambda: scope)
    monkeypatch.setattr(smoke_module, "load_config", lambda *_a, **_k: UnifiedConfig())
    monkeypatch.delenv("RALPH_BROKER_SECRET", raising=False)

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: UnifiedConfig) -> FakeRegistry:
            return cls()

        def get(self, name: str) -> AgentConfig | None:
            if name == "kimi/kimi-code/kimi-for-coding":
                return AgentConfig(cmd="kimi", transport=AgentTransport.KIMI)
            return None

    monkeypatch.setattr(smoke_module, "AgentRegistry", FakeRegistry)

    def fake_run_smoke_plumbing(**kwargs: object) -> smoke_module.SmokeRunResult:
        assert os.environ.get("RALPH_BROKER_SECRET"), (
            "smoke composition root must mint RALPH_BROKER_SECRET before plumbing runs"
        )
        return smoke_module.SmokeRunResult(
            agent_name="kimi/kimi-code/kimi-for-coding",
            transport="kimi",
            output_file=tmp_path / "tmp" / "interactive-kimi-smoke" / "todo-list.js",
            file_created=True,
            session_id="kimi-sess-1",
            explicit_completion_seen=Evidence(True, Provenance.WIRE, "test fixture"),
            raw_line_count=1,
            parsed_event_count=1,
            tool_activity_seen=Evidence(True, Provenance.WIRE, "test fixture"),
            artifact_submitted=Evidence(True, Provenance.WIRE, "test fixture"),
            meaningful_output_lines=["ok"],
            errors=[],
        )

    monkeypatch.setattr(smoke_module, "run_smoke_plumbing", fake_run_smoke_plumbing)

    exit_code = smoke_module.smoke_harness_agent_command(
        "kimi/kimi-code/kimi-for-coding",
        display_context=None,
    )

    assert exit_code == 0


@pytest.mark.timeout_seconds(10)
def test_smoke_harness_agent_command_preserves_operator_broker_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stream = _attach_console(monkeypatch)
    del stream
    scope = WorkspaceScope(tmp_path)
    monkeypatch.setattr(smoke_module, "resolve_workspace_scope", lambda: scope)
    monkeypatch.setattr(smoke_module, "load_config", lambda *_a, **_k: UnifiedConfig())
    monkeypatch.setenv("RALPH_BROKER_SECRET", "operator-secret")

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: UnifiedConfig) -> FakeRegistry:
            return cls()

        def get(self, name: str) -> AgentConfig | None:
            if name == "kimi/kimi-code/kimi-for-coding":
                return AgentConfig(cmd="kimi", transport=AgentTransport.KIMI)
            return None

    monkeypatch.setattr(smoke_module, "AgentRegistry", FakeRegistry)

    def fake_run_smoke_plumbing(**kwargs: object) -> smoke_module.SmokeRunResult:
        del kwargs
        assert os.environ.get("RALPH_BROKER_SECRET") == "operator-secret"
        return smoke_module.SmokeRunResult(
            agent_name="kimi/kimi-code/kimi-for-coding",
            transport="kimi",
            output_file=tmp_path / "tmp" / "interactive-kimi-smoke" / "todo-list.js",
            file_created=True,
            session_id="kimi-sess-1",
            explicit_completion_seen=Evidence(True, Provenance.WIRE, "test fixture"),
            raw_line_count=1,
            parsed_event_count=1,
            tool_activity_seen=Evidence(True, Provenance.WIRE, "test fixture"),
            artifact_submitted=Evidence(True, Provenance.WIRE, "test fixture"),
            meaningful_output_lines=["ok"],
            errors=[],
        )

    monkeypatch.setattr(smoke_module, "run_smoke_plumbing", fake_run_smoke_plumbing)

    exit_code = smoke_module.smoke_harness_agent_command(
        "kimi/kimi-code/kimi-for-coding",
        display_context=None,
    )

    assert exit_code == 0


@pytest.mark.timeout_seconds(10)
def test_smoke_minted_broker_secret_never_reaches_agent_subprocess_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from ralph.agents.invoke._process_reader import _subprocess_env

    monkeypatch.setenv("RALPH_BROKER_SECRET", "smoke-minted-secret")

    env = _subprocess_env(None)

    assert "RALPH_BROKER_SECRET" not in env
    assert os.environ.get("RALPH_BROKER_SECRET") == "smoke-minted-secret"
