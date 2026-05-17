from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console

from ralph.cli.commands import smoke as smoke_module
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import DisplayContext
from ralph.display.theme import RALPH_THEME
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    import pytest


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
    monkeypatch.setattr(smoke_module, "make_display_context", lambda **_kwargs: ctx)
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
    monkeypatch.setattr(smoke_module, "resolve_workspace_scope", lambda: scope)
    monkeypatch.setattr(smoke_module, "load_config", lambda *args, **kwargs: UnifiedConfig())

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
        def agent_endpoint_uri(self) -> str:
            return "http://127.0.0.1:9999/mcp"

        def shutdown(self) -> None:
            bridge_shutdown.append(True)

    def fake_invoke_agent(config: AgentConfig, prompt_file: str, *, options: object=None) -> object:
        assert config.yolo_flag == "--dangerously-skip-permissions"
        assert options is not None
        assert options.extra_env is not None
        assert options.extra_env[smoke_module.MCP_ENDPOINT_ENV] == "http://127.0.0.1:9999/mcp"
        assert options.idle_timeout_seconds == smoke_module._SMOKE_IDLE_TIMEOUT_SECONDS
        assert options.max_session_seconds == smoke_module._SMOKE_MAX_SESSION_SECONDS
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
        return iter(
            [
                '{"type":"assistant","message":{"type":"message","content":'
                '[{"type":"text","text":"I am creating the todo list now."}]}}\n',
                '{"type":"assistant","message":{"type":"message","content":'
                '[{"type":"text","text":"The file has been written successfully."}]}}\n',
                "Claude session ready. Session ID: interactive-smoke-session\n",
                "claude tool: write_file\n",
                "Task declared complete: session_id=interactive-smoke-session, "
                "summary=done, timestamp=1\n",
            ]
        )

    monkeypatch.setattr(smoke_module, "AgentRegistry", FakeRegistry)
    monkeypatch.setattr(smoke_module, "invoke_agent", fake_invoke_agent)
    monkeypatch.setattr(
        smoke_module,
        "_start_smoke_bridge",
        lambda _root, *, config: FakeBridge(),
    )

    exit_code = smoke_module.smoke_interactive_claude_command(display_context=None)

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
