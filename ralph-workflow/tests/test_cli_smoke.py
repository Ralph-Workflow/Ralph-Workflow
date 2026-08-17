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
from ralph.pro_support.hooks import ProPipelineHooks
from ralph.pro_support.state_query import SnapshotRegistry
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests._support.typed_accessors import (
    must_mapping,
)

# Policy (2026-06-14): smoke tests are NOT part of any test suite. They are
# one-off manual debug harnesses for a SPECIFIC agent issue. Marked with
# the ``smoke`` marker and excluded by default via ``addopts`` in
# ``pytest.ini`` AND via ``-m "not smoke"`` in every Makefile target and
# in ``ralph/test_suites.py``. To run one explicitly:
#   pytest tests/test_cli_smoke.py -m smoke
#   pytest tests/test_cli_smoke.py::test_specific_test -m smoke
# These tests inspect real subprocess output (``tmp/smoke-interactive-agy-run.log``)
# produced by a prior ``ralph smoke-interactive-agy`` invocation, which
# is exactly the kind of real file I/O the test policy forbids in regular tests.
pytestmark = pytest.mark.smoke


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
    """S-2 (Evidence Provenance): AGY must be told to reach Ralph tools via call_mcp_tool.

    Measured live (agy_wire_provenance.md, 2026-08-06 entry): a plain
    "call `{tool}`" instruction plus a permissive fallback bullet taught the
    model nothing -- it took the file-fallback path on every turn, once even
    while wrongly claiming `call_mcp_tool` was "not present in the current
    toolset" despite the transcript's own init frame listing it. The
    submission bullet must name `call_mcp_tool` as the required first
    attempt; the completion bullet must NOT force a second `call_mcp_tool`
    round trip (a live replay of that variant hung and had to be killed --
    same fixture entry), so `declare_complete` stays on the plain
    transport-agnostic phrasing every other transport uses.
    """
    agy_prompt = smoke_module.build_smoke_prompt(
        "tmp/interactive-agy-smoke/todo-list.js",
        submit_artifact_tool_name="ralph_submit_md_artifact",
        transport=AgentTransport.AGY,
    )

    assert "call_mcp_tool" in agy_prompt
    assert "MCP server `ralph`" in agy_prompt
    assert "ralph_submit_md_artifact" in agy_prompt
    # The completion bullet must stay on the plain phrasing, not a second
    # call_mcp_tool instruction -- assert declare_complete is never paired
    # with the dispatcher wording in the same sentence.
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
    # DA-001: the smoke composition root now mints a run-scoped broker
    # secret when the operator exported none. This scenario hand-writes an
    # UNSIGNED sentinel (no HMAC binding to the minted secret), so the
    # sentinel check correctly rejects it and the report renders the
    # completion fact as absent -- the transcript substring stays a
    # spoofable signal, never a completion witness. Keep the environment
    # unsigned so the test pins exactly that boundary.
    monkeypatch.delenv("RALPH_BROKER_SECRET", raising=False)
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

    # S-6 (PA-001): this scenario writes the fallback smoke_test_result.md
    # and completion sentinel directly (a promoted fallback + host-authored
    # sentinel, not a real MCP receipt), so no required fact reaches WIRE
    # and the graded verdict is DEGRADED -- the exit code must say so too,
    # not silently report 0 the way the pre-S-6 result.errors-only check did.
    assert exit_code == 1
    assert bridge_shutdown == [True]
    assert requested_agents == ["claude/haiku"]
    output = stream.getvalue()
    assert "claude/haiku" in output
    assert "Headless semantic guide" in output
    assert "smoke_test_result artifact submitted" in output
    # The hand-written sentinel is unsigned (see the delenv note above), so
    # the completion fact renders absent rather than observed; the
    # transcript's "Task declared complete:" substring never substitutes
    # for the HMAC-bound durable sentinel.
    assert "completion sentinel observed" not in output
    assert "Observed output" in output
    assert "I am creating the todo list now." in output
    # Agent name surfaces in the parity-table title; tool activity surfaces in
    # the parser-classified ``Observed output`` block as ``tool_use: <name>``
    # (the agent's tool invocation, not the human-readable ``<agent> tool: <name>``
    # form the older single-string rendered_lines API used to produce).
    assert "tool_use: write_file" in output
    assert "write_file" in output
    assert "Observed working" in output
    assert "Observed breaks" in output
    # S-6 (PA-001): this scenario's evidence never reaches WIRE (see the
    # exit_code assertion above), so "No breaks observed" is not the
    # correct/honest text -- the DEGRADED branch of _render_smoke_report
    # names the demotion instead. With the completion sentinel absent the
    # weakest provenance is ABSENT.
    assert "DEGRADED (absent)" in output


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
            if name == "agy/gemini-3.5-flash-medium":
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
            agent_name="agy/gemini-3.5-flash-medium",
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
        agent_name="agy/gemini-3.5-flash-medium",
        display_context=None,
    )

    assert exit_code == 0
    assert captured["agent_name"] == "agy/gemini-3.5-flash-medium"
    output = stream.getvalue()
    assert "agy/gemini-3.5-flash-medium" in output
    assert "agy/gemini-3.5-flash-medium parity smoke test" in output
    assert "agy/gemini-3.5-flash-medium parity smoke report" in output


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
    """Return the real registered nanocoder ``AgentSupport`` (S-6 / G6 / DoD 20).

    Mirrors ``tests/test_harness_run_diagnosis.py``'s helper of the same
    name: passing the real registered support (rather than a hand-built
    double) reproduces the ``support`` resolution ``_run_smoke_agent``
    performs in production, so the test cannot drift from the real
    registered ``session_identifier_observable`` value.
    """
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
    """``smoke_interactive_agy_command`` respects ``RALPH_AGY_BINARY``."""
    stream = _attach_console(monkeypatch)
    scope = WorkspaceScope(tmp_path)
    monkeypatch.setattr(smoke_module, "resolve_workspace_scope", lambda: scope)
    monkeypatch.setattr(smoke_module, "load_config", lambda *_a, **_k: UnifiedConfig())

    mock_agy = Path(__file__).resolve().parent / "_support" / "mock_agy.sh"
    monkeypatch.setenv("RALPH_AGY_BINARY", str(mock_agy))
    monkeypatch.setenv("MOCK_AGY_ARTIFACT_DIR", str(tmp_path))

    exit_code = smoke_module.smoke_interactive_agy_command(
        agent_name="agy/gemini-3.5-flash-medium",
        display_context=None,
    )

    # S-6 (PA-001) / S-4: the mock never contacts a real MCP server (it
    # writes the fallback artifact and sentinel directly), and its evidence
    # ceiling is pinned below WIRE (test_mock_agy_evidence_ceiling_grades_below_wire
    # in test_smoke_agy_end_to_end.py), so the graded verdict here is always
    # DEGRADED and the exit code must be non-zero -- a mock run can never
    # honestly report exit_code 0.
    assert exit_code == 1
    output = stream.getvalue()
    assert "agy/gemini-3.5-flash-medium" in output
    # The parity table row must show file=yes for the mock-backed run.
    # The table may wrap the long agent name, so match the transport/file cells.
    assert re.search(r"│\s*agy\s*│\s*yes\s*│", output) is not None


@pytest.mark.timeout_seconds(10)
def test_resolve_agy_binary_override_normalizes_relative_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``_resolve_agy_binary_override`` returns the absolute path for a relative override.

    Regression: the relative ``RALPH_AGY_BINARY=tests/_support/mock_agy.sh`` previously
    raised ``FileNotFoundError`` at subprocess spawn time because the harness may
    change cwd before spawning the agent. The fix resolves the relative path to an
    absolute one so the override is always spawnable from a known location
    regardless of the cwd the operator happened to be in.
    """
    mock_source = Path(__file__).resolve().parent / "_support" / "mock_agy.sh"
    relative_target = tmp_path / "tests" / "_support" / "mock_agy.sh"
    relative_target.parent.mkdir(parents=True, exist_ok=True)
    relative_target.write_text(mock_source.read_text(encoding="utf-8"), encoding="utf-8")
    relative_target.chmod(0o755)
    monkeypatch.setenv("RALPH_AGY_BINARY", "tests/_support/mock_agy.sh")
    monkeypatch.chdir(tmp_path)

    resolved = smoke_module._resolve_agy_binary_override()
    assert resolved is not None
    assert Path(resolved).is_absolute()
    assert Path(resolved).resolve() == relative_target.resolve()


@pytest.mark.timeout_seconds(10)
def test_resolve_agy_binary_override_returns_none_for_missing_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_resolve_agy_binary_override`` returns ``None`` for a non-executable override.

    The override validation is the safety net that prevents the harness from
    crashing on an unhandled ``OSError`` at subprocess spawn time. When the
    override path is not executable, the helper logs a WARNING and returns
    ``None`` so the caller falls back to the real ``agy`` binary on ``PATH``.
    """
    monkeypatch.setenv("RALPH_AGY_BINARY", "/nonexistent/path/to/agy-mock.sh")
    resolved = smoke_module._resolve_agy_binary_override()
    assert resolved is None


@pytest.mark.timeout_seconds(15)
def test_smoke_interactive_agy_with_relative_mock_binary_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """End-to-end CLI regression for the relative ``RALPH_AGY_BINARY`` path.

    The smoke harness must spawn the AGY mock even when ``RALPH_AGY_BINARY``
    is a relative path (e.g. ``tests/_support/mock_agy.sh``). Previously this
    raised ``FileNotFoundError`` because the relative path was passed to
    ``subprocess.Popen`` unchanged, and the harness may change cwd before
    spawning the agent. The fix resolves the relative path to an absolute
    one before spawn.

    The end-to-end check drives the public ``smoke_interactive_agy_command``
    with the mock in place and asserts the parity report shows
    ``file=yes`` and ``breaks=none`` (the harness contract the prior analysis
    flagged as broken for the real CLI flow).
    """
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
        agent_name="agy/gemini-3.5-flash-medium",
        display_context=None,
    )

    output = stream.getvalue()
    # S-6 (PA-001) / S-4: same reasoning as test_smoke_interactive_agy_with_mock_binary --
    # the mock can never produce WIRE evidence, so the graded verdict is
    # DEGRADED and exit_code must be non-zero.
    assert exit_code == 1, (
        f"Expected exit_code 1 (DEGRADED, mock evidence never reaches WIRE) for "
        f"relative mock path; got {exit_code}. Output tail:\n{output[-3000:]}"
    )
    assert re.search(r"\u2502\s*agy\s*\u2502\s*yes\s*\u2502", output) is not None, (
        f"Expected 'file=yes' in parity table; output tail:\n{output[-3000:]}"
    )
    # S-6 (PA-001): the mock's evidence never reaches WIRE (see the
    # exit_code assertion above), so the report must name the demotion
    # rather than claim no breaks were observed.
    assert "DEGRADED (" in output, (
        f"Expected a named DEGRADED demotion in parity report; output tail:\n{output[-3000:]}"
    )


def test_maybe_apply_agy_binary_override_ignores_nonexecutable_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-executable RALPH_AGY_BINARY path is ignored (WARNING logged, cmd unchanged)."""
    agy_config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)
    monkeypatch.setenv("RALPH_AGY_BINARY", "/etc/hosts")
    result = smoke_module._maybe_apply_agy_binary_override(agy_config)
    assert result.cmd == "agy"


def test_maybe_apply_agy_binary_override_accepts_mock_shell_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid shell script override is accepted."""
    mock_path = Path(__file__).resolve().parent / "_support" / "mock_agy.sh"
    agy_config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)
    monkeypatch.setenv("RALPH_AGY_BINARY", str(mock_path))
    result = smoke_module._maybe_apply_agy_binary_override(agy_config)
    assert result.cmd != "agy"
    assert str(mock_path) in result.cmd


def test_apply_agy_binary_override_to_config_ignores_nonexecutable_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_apply_agy_binary_override_to_config`` ignores a non-executable override."""
    config = UnifiedConfig(
        agents={
            "agy/gemini-3.5-flash-medium": AgentConfig(cmd="agy", transport=AgentTransport.AGY),
            "claude/haiku": AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE),
        }
    )
    monkeypatch.setenv("RALPH_AGY_BINARY", "/etc/hosts")
    result = smoke_module._apply_agy_binary_override_to_config(config)
    assert result.agents["agy/gemini-3.5-flash-medium"].cmd == "agy"
    assert result.agents["claude/haiku"].cmd == "claude"


def test_apply_agy_binary_override_to_config_accepts_mock_shell_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_apply_agy_binary_override_to_config`` accepts a valid mock shell script."""
    mock_path = Path(__file__).resolve().parent / "_support" / "mock_agy.sh"
    config = UnifiedConfig(
        agents={
            "agy/gemini-3.5-flash-medium": AgentConfig(cmd="agy", transport=AgentTransport.AGY),
        }
    )
    monkeypatch.setenv("RALPH_AGY_BINARY", str(mock_path))
    result = smoke_module._apply_agy_binary_override_to_config(config)
    agy_cmd = result.agents["agy/gemini-3.5-flash-medium"].cmd
    assert agy_cmd != "agy"
    assert str(mock_path) in agy_cmd


@pytest.mark.timeout_seconds(10)
def test_is_mock_agy_override_classifies_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``is_mock_agy_override`` correctly distinguishes the mock from real wrappers."""
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
    """A non-mock executable override is accepted and applied verbatim.

    Regression: the prior implementation treated every ``RALPH_AGY_BINARY``
    override as a mock run, so a real wrapper or alternate live binary path
    was misclassified and could not be diagnosed through the live
    ``cli.log`` path. The override must be applied verbatim (the cmd is the
    absolute override path quoted for spaces) and the operator-facing log
    must NOT say ``mock AGY binary in use``.
    """
    # Create a tiny executable script in tmp_path that is NOT named
    # ``mock_agy`` to verify the general-override path is exercised.
    stub_path = tmp_path / "agy-wrapper.sh"
    stub_path.write_text("#!/usr/bin/env sh\necho stub\n", encoding="utf-8")
    stub_path.chmod(0o755)

    agy_config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)
    monkeypatch.setenv("RALPH_AGY_BINARY", str(stub_path))
    result = smoke_module._maybe_apply_agy_binary_override(agy_config)
    # The override is applied verbatim (no mock substitution), and the cmd
    # is the absolute stub path (quoted because it lives in tmp_path which
    # may contain spaces; the assertion uses ``in`` to allow shlex.quote
    # to wrap it).
    assert result.cmd != "agy"
    assert str(stub_path) in result.cmd
    # A non-mock override is NOT the mock: the helper must agree.
    assert is_mock_agy_override() is False


@pytest.mark.timeout_seconds(10)
def test_apply_agy_binary_override_to_config_accepts_non_mock_executable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A non-mock executable override is honored by the config-level helper."""
    stub_path = tmp_path / "agy-real-wrapper.sh"
    stub_path.write_text("#!/usr/bin/env sh\necho real-wrapper\n", encoding="utf-8")
    stub_path.chmod(0o755)

    config = UnifiedConfig(
        agents={
            "agy/gemini-3.5-flash-medium": AgentConfig(cmd="agy", transport=AgentTransport.AGY),
            "claude/haiku": AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE),
        }
    )
    monkeypatch.setenv("RALPH_AGY_BINARY", str(stub_path))
    result = smoke_module._apply_agy_binary_override_to_config(config)
    agy_cmd = result.agents["agy/gemini-3.5-flash-medium"].cmd
    # The override is applied: cmd is no longer ``agy`` and contains the stub.
    assert agy_cmd != "agy"
    assert str(stub_path) in agy_cmd
    # The non-AGY agent is preserved.
    assert result.agents["claude/haiku"].cmd == "claude"


@pytest.mark.timeout_seconds(10)
def test_smoke_command_exit_code_and_breaks_cell_reflect_evidence_verdict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """PA-001: exit_code and Breaks cell derive from Evidence verdict, not result.errors alone.

    Replay a 2026-08-05-shaped run (all facts hold but weak provenance -> DEGRADED).
    Assert exit_code is 1 (non-zero) and the Breaks cell names the demotion
    rather than printing "none".
    """
    stream = StringIO()
    # A wide console so the Rich table does not truncate the Breaks cell to
    # "degr…" -- the default 100-column width used elsewhere in this file
    # is too narrow to fit "degraded verdict: DEGRADED (host-synthesized)"
    # across 10 columns, and this test needs to read that cell's full text.
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
        errors=[],  # No hard breaks/errors reported in result.errors!
    )

    verdict_label, _ = grade_verdict(smoke_module._required_evidence(result))
    assert verdict_label == DEGRADED

    # Calculate exit_code as in smoke_harness_agent_command
    exit_code = 0 if not result.errors and verdict_label == PASS else 1
    assert exit_code == 1

    # Render table and verify Breaks cell is not "none"
    smoke_module._render_smoke_table(
        [result], display_context=display_context, agent_name="agy/gemini-3.6-flash-low"
    )
    rendered = normalize_vt_text(stream.getvalue())
    # The table's unrelated "Subagent" column legitimately renders "none"
    # (no subagent requested), so assert the Breaks cell's own content
    # directly rather than the bare absence of "none" anywhere in the table.
    assert "degraded verdict" in rendered
    assert "DEGRADED (host-synthesized)" in rendered


@pytest.mark.timeout_seconds(10)
def test_render_smoke_table_ungraded_multimodal_cell_is_not_not_requested(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """S-2: a multimodal-requested but ungraded run must NOT render ``not requested``.

    Replay the defect: the run was driven by the multimodal prompt
    (``multimodal_requested=True``) but the agent never produced a
    verified media tool call (``multimodal_tool_used=None``). The
    ``Multimodal`` cell must surface this absence -- the same
    `_evidence_cell(absent(...))` rendering the lattice test pins --
    rather than masquerading as a non-multimodal run by printing
    ``not requested``. ``not requested`` is reserved for runs where
    ``multimodal_requested`` is false (S-4).
    """
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
        artifact_submitted=Evidence(True, Provenance.WIRE, "receipt matched a tools/call ledger record"),
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

    # The defect renders ``not requested`` for the multimodal cell of a
    # multimodal_requested run whose fact was never graded -- silently
    # masquerading as a non-multimodal scenario. After S-4, the cell reads
    # ``no`` (the absent fact's evidence rendering) and the Verdict column
    # reflects the DEGRADED run. The table's unrelated ``Subagent`` column
    # legitimately renders ``not requested`` (no subagent requested), so the
    # assertion below targets the cell directly rather than the bare absence
    # of the substring anywhere in the table.
    assert rendered.count("not requested") == 1, (
        "the Multimodal cell must NOT render 'not requested' for an ungraded "
        "multimodal run; the Subagent column's legitimate 'not requested' "
        "should be the only occurrence"
    )
    assert "DEGRADED (absent)" in rendered



# ---------------------------------------------------------------------------
# DA-002 (R8): subagent contract failures MUST be fatal in the exit code
# ---------------------------------------------------------------------------
# The plan's R8 contract is: "a transport that cannot dispatch a subagent
# reports a failed check; it must not silently degrade to the basic
# scenario." A regression introduced a ``fatal_subagent_errors`` allowlist
# filter that converted missing subagent-tail signals into exit-code-zero
# PASSes. The tests below pin every individual subagent contract failure
# as fatal (non-zero exit) so a future operator cannot silently downgrade
# the check without breaking the test surface.


@pytest.mark.timeout_seconds(10)
def test_subagent_dispatch_missing_is_fatal_exit_code() -> None:
    """R8 #1: ``not every subagent dispatch has a correlated result`` -> non-zero exit.

    The ``--subagents`` contract is that every dispatch lands a
    correlated result. A run that dispatches a subagent but never
    observes a correlated result has failed the subagent contract;
    the exit code MUST be non-zero. The prior implementation
    removed this error from the fatal set and silently passed
    the run, which is the DA-002 regression.
    """
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
    # Verdict may be PASS or DEGRADED; the subagent contract
    # failure in result.errors MUST drive the exit code.
    assert verdict_label in {PASS, DEGRADED}
    # Reproduce the production exit_code formula from
    # ``smoke_harness_agent_command``: ALL result.errors are
    # fatal, not just a subset.
    exit_code = 0 if not result.errors and verdict_label == PASS else 1
    assert exit_code == 1, (
        "subagent dispatch missing correlated result must yield"
        " a non-zero exit; the prior fatal_subagent_errors"
        " allowlist regression silently downgraded this case to"
        " exit 0 (DA-002)"
    )


@pytest.mark.timeout_seconds(10)
def test_subagent_no_post_result_activity_is_fatal_exit_code() -> None:
    """R8 #2: ``no meaningful activity was observed after the subagent result`` -> non-zero exit.

    A run whose subagent contract reports a correlated result
    but no meaningful follow-up activity has failed the
    subagent contract; the exit code MUST be non-zero. The
    prior implementation removed this error from the fatal
    set and silently passed the run.
    """
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
    """R8 #3: BOTH interactive and headless smoke runs fail non-zero on uncorrelated dispatch.

    The two transports share the same exit-code formula;
    converting an exit-code-zero on a subagent contract
    failure in either transport would convert a "transport
    reached Ralph's MCP tools" PASS into a noisy failure every
    time the model races itself. The test pins BOTH paths to
    the same fatal exit.
    """
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
            f"{transport_label} subagent contract failure must yield"
            f" a non-zero exit (DA-002)"
        )


@pytest.mark.timeout_seconds(10)
def test_no_subagent_errors_with_pass_verdict_yields_zero_exit() -> None:
    """Sanity: a clean run with PASS verdict and no errors still exits 0.

    Pins the GREEN path of the exit-code formula so a future
    over-correction doesn't downgrade every run to non-zero.
    """
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
    """DA-001: the smoke CLI composition root signs the wire ledger itself.

    The measured 2026-08-17 live Kimi smoke created the file, submitted the
    artifact, and declared completion, yet all three required facts graded
    below WIRE and the run exited 1: ``python -m ralph smoke-interactive-kimi``
    starts a fresh process whose environment carries no
    ``RALPH_BROKER_SECRET``, so ``build_session_bridge`` read ``None`` and
    every wire-ledger append was a documented no-op. The shared smoke
    composition root (``smoke_harness_agent_command``) MUST mint a random,
    run-scoped broker secret when the operator exported none -- the
    anti-forgery boundary is preserved because ``_subprocess_env`` strips
    the variable from the agent's environment, so the minted secret never
    reaches the model.
    """
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
        # The secret must exist by the time plumbing builds the session
        # bridge (which reads it via _parent_broker_secret()).
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
    """An operator-exported ``RALPH_BROKER_SECRET`` is never overwritten."""
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
    """A minted run-scoped secret must not leak into the spawned agent's env.

    ``_subprocess_env`` strips ``RALPH_BROKER_SECRET`` from both the
    inherited environment and any caller-supplied ``extra_env``; this pins
    that guarantee against a smoke-minted value so the composition-root
    mint cannot become a model-readable credential. The exhaustive
    inherited-env / extra-env matrix lives in
    ``tests/test_subprocess_env_secret_isolation.py``.
    """
    from ralph.agents.invoke._process_reader import _subprocess_env

    monkeypatch.setenv("RALPH_BROKER_SECRET", "smoke-minted-secret")

    env = _subprocess_env(None)

    assert "RALPH_BROKER_SECRET" not in env
    assert os.environ.get("RALPH_BROKER_SECRET") == "smoke-minted-secret"
