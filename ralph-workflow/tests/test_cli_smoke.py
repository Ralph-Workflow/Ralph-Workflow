from __future__ import annotations

import re
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, cast

from rich.console import Console

if TYPE_CHECKING:
    from collections import deque

    import pytest

    from ralph.pipeline.factory import PipelineDeps

from ralph.cli.commands import smoke as smoke_module
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import DisplayContext
from ralph.display.theme import RALPH_THEME
from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.plumbing import smoke_plumbing as smoke_plumbing_module
from ralph.pro_support.hooks import ProPipelineHooks
from ralph.pro_support.state_query import SnapshotRegistry
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps


class _FakePipelineFactory:
    """Conforms to ``PipelineFactory`` and records every build call."""

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
        mode="wide",
        narrow=False,
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
        submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact",
    )

    assert "tmp/interactive-claude-smoke/todo-list.js" in prompt
    assert "JavaScript todo list" in prompt
    assert "declare_complete" in prompt
    assert "smoke_test_result" in prompt
    assert "mcp__ralph__ralph_submit_artifact" in prompt
    assert 'status: one of "passed", "failed", or "partial"' in prompt
    assert 'output_file: "tmp/interactive-claude-smoke/todo-list.js"' in prompt
    assert "observed_working" in prompt
    assert "observed_breaks" in prompt
    assert "headless_guide_checks" in prompt


def test_render_smoke_report_surfaces_working_and_broken_observations() -> None:
    result = smoke_module.SmokeRunResult(
        agent_name="claude/haiku",
        transport="claude_interactive",
        output_file=Path("tmp/interactive-claude-smoke/todo-list.js"),
        file_created=True,
        session_id="sess-1",
        explicit_completion_seen=False,
        raw_line_count=2,
        parsed_event_count=1,
        tool_activity_seen=False,
        artifact_submitted=False,
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
    scope = WorkspaceScope(tmp_path)
    def _resolve_workspace_scope() -> WorkspaceScope:
        return scope

    def _load_config(*_args: object, **_kwargs: object) -> UnifiedConfig:
        return UnifiedConfig()

    monkeypatch.setattr(smoke_module, "resolve_workspace_scope", _resolve_workspace_scope)
    monkeypatch.setattr(smoke_module, "load_config", _load_config)

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
                cast("deque[str]", raw_sink).append(line)
        if rendered_sink is not None:
            for line in rendered_lines:
                cast("deque[str]", rendered_sink).append(line)
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
                mode="wide",
                narrow=False,
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

    assert exit_code == 0
    assert bridge_shutdown == [True]
    assert requested_agents == ["claude/haiku"]
    output = stream.getvalue()
    assert "claude/haiku" in output
    assert "Headless semantic guide" in output
    assert "smoke_test_result artifact submitted" in output
    assert "Observed output" in output
    assert "I am creating the todo list now." in output
    assert "claude/haiku tool" in output
    assert "write_file" in output
    assert "Observed working" in output
    assert "Observed breaks" in output
    assert "No breaks observed" in output


def test_smoke_interactive_claude_command_forwards_pro_hooks_and_model_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The smoke command forwards injected Pro hooks and model identity into the
    shared pipeline dependency factory so plumbing uses the same initialization
    path as the main pipeline.
    """
    _attach_console(monkeypatch)
    scope = WorkspaceScope(tmp_path)
    monkeypatch.setattr(smoke_module, "resolve_workspace_scope", lambda: scope)
    monkeypatch.setattr(smoke_module, "load_config", lambda *_a, **_k: UnifiedConfig())
    monkeypatch.setattr(
        smoke_module,
        "submit_artifact_tool_name_for_transport",
        lambda _transport: "mcp__ralph__ralph_submit_artifact",
    )
    monkeypatch.setattr(
        smoke_module,
        "_build_smoke_prompt",
        lambda _output_relpath, *, submit_artifact_tool_name: "prompt",
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
            mode="wide",
            narrow=False,
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
            explicit_completion_seen=True,
            raw_line_count=1,
            parsed_event_count=1,
            tool_activity_seen=True,
            artifact_submitted=True,
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
    plumbing_kwargs = cast("dict[str, object]", captured["plumbing_kwargs"])
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
            if name == "agy/gemini-3.5-flash-low":
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
            agent_name="agy/gemini-3.5-flash-low",
            transport="agy",
            output_file=tmp_path / "tmp" / "interactive-agy-smoke" / "todo-list.js",
            file_created=True,
            session_id="agy-sess-1",
            explicit_completion_seen=True,
            raw_line_count=1,
            parsed_event_count=1,
            tool_activity_seen=True,
            artifact_submitted=True,
            meaningful_output_lines=["ok"],
            errors=[],
        )

    monkeypatch.setattr(smoke_module, "run_smoke_plumbing", fake_run_smoke_plumbing)

    exit_code = smoke_module.smoke_interactive_agy_command(display_context=None)

    assert exit_code == 0
    assert captured["agent_name"] == "agy/gemini-3.5-flash-low"
    output = stream.getvalue()
    assert "agy/gemini-3.5-flash-low" in output
    assert "agy/gemini-3.5-flash-low parity smoke test" in output
    assert "agy/gemini-3.5-flash-low parity smoke report" in output


def test_smoke_interactive_agy_documents_live_run_outcome() -> None:
    """The captured AGY smoke run log reflects the fixed end-to-end outcome.

    After aligning the smoke detector with AGY's actual --print behaviour, the
    live run exits 0 and reports file creation, parser events, tool activity
    (via the persisted artifact), and artifact submission. The session column
    remains 'missing' because AGY headless --print does not emit a transport
    session ID in stdout (verified in tmp/agy-live-transcript.txt).
    """
    log_path = Path(__file__).resolve().parents[1] / "tmp" / "smoke-interactive-agy-run.log"
    assert log_path.exists(), (
        "Live AGY smoke run log not captured in this environment"
    )

    log_text = log_path.read_text(encoding="utf-8")
    assert "EXIT_CODE=0" in log_text, (
        f"Live AGY smoke run did not exit 0; see {log_path}"
    )

    agy_row = next(
        (
            line
            for line in log_text.splitlines()
            if "agy/gem" in line and "missing" in line and ("│" in line or "┃" in line)
        ),
        None,
    )
    assert agy_row is not None, "AGY parity table row not found in smoke log"

    cells = [cell.strip() for cell in re.split(r"[│┃]", agy_row) if cell.strip()]
    # Expected cells: agent, transport, file, session, parser events, tool
    # activity, artifact, breaks (after stripping table borders).
    assert len(cells) >= 7, f"Unexpected AGY table row shape: {cells}"
    assert cells[2] == "yes", f"Expected file=yes, got: {cells}"
    assert int(cells[4]) > 0, f"Expected parser events > 0, got: {cells}"
    assert cells[5] == "yes", f"Expected tool activity=yes, got: {cells}"
    assert cells[6] == "yes", f"Expected artifact=yes, got: {cells}"
