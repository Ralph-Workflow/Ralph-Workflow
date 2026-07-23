"""Tests for the smoke-test plumbing core.

These tests are black-box and use injected fakes only: no real subprocess,
no real network, no time.sleep, no real file I/O.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from ralph.agents.invoke import InvokeOptions
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.mcp.artifacts.canonical_submit import promote_fallback_artifact
from ralph.mcp.artifacts.smoke_test_result import read_smoke_test_result_artifact
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.factory import PipelineDeps
from ralph.pipeline.plumbing import smoke_plumbing as smoke_plumbing_module
from ralph.pipeline.plumbing.smoke_run_params import SmokeRunParams

if TYPE_CHECKING:
    from collections import deque
    from collections.abc import Callable

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


def test_build_smoke_prompt_adds_shared_subagent_scenario_only_when_requested() -> None:
    basic = smoke_plumbing_module._build_smoke_prompt(
        "tmp/interactive-claude-smoke/todo-list.js",
        submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact",
    )
    subagents = smoke_plumbing_module._build_smoke_prompt(
        "tmp/interactive-claude-smoke/todo-list.js",
        submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact",
        subagents=True,
    )

    assert "delegate exactly one bounded, read-only task" not in basic
    assert "delegate exactly one bounded, read-only task" in subagents
    assert "After the subagent result" in subagents
    assert "mcp__ralph__ralph_submit_artifact" in subagents
    assert "declare_complete" in subagents


def test_build_smoke_prompt_uses_custom_subagent_task_without_losing_harness_contract() -> None:
    prompt = smoke_plumbing_module._build_smoke_prompt(
        "tmp/interactive-claude-smoke/todo-list.js",
        submit_artifact_tool_name="mcp__ralph__ralph_submit_artifact",
        subagents=True,
        subagent_prompt="Inspect the parser and report two possible edge cases.",
    )

    assert "Inspect the parser and report two possible edge cases." in prompt
    assert "tmp/interactive-claude-smoke/todo-list.js" in prompt
    assert "smoke_test_result" in prompt
    assert "declare_complete" in prompt


def test_subagent_smoke_evidence_requires_dispatch_result_and_later_activity() -> None:
    config = AgentConfig(
        cmd="claude",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    )
    lines = [
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_subagent",
                            "name": "Agent",
                            "input": {"prompt": "inspect parser"},
                        }
                    ]
                },
            }
        ),
        json.dumps(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_subagent",
                            "content": "inspection complete",
                        }
                    ]
                },
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_write",
                            "name": "Write",
                            "input": {"file_path": "tmp/todo-list.js"},
                        }
                    ]
                },
            }
        ),
    ]

    evidence = smoke_plumbing_module._subagent_smoke_evidence(config, lines)

    assert evidence.dispatch_seen is True
    assert evidence.result_seen is True
    assert evidence.post_result_activity_seen is True
    assert smoke_plumbing_module._subagent_smoke_error(evidence) is None


@pytest.mark.parametrize(
    ("lines", "expected_error"),
    [
        ([], "subagent dispatch was not observed"),
        (
            [
                '{"type":"assistant","message":{"content":['
                '{"type":"tool_use","id":"toolu_subagent","name":"Task",'
                '"input":{"prompt":"inspect"}}]}}'
            ],
            "subagent result was not observed",
        ),
        (
            [
                '{"type":"assistant","message":{"content":['
                '{"type":"tool_use","id":"toolu_subagent","name":"Task",'
                '"input":{"prompt":"inspect"}}]}}',
                '{"type":"user","message":{"content":['
                '{"type":"tool_result","tool_use_id":"toolu_subagent",'
                '"content":"done"}]}}',
            ],
            "no meaningful activity was observed after the subagent result",
        ),
    ],
)
def test_subagent_smoke_evidence_reports_first_missing_signal(
    lines: list[str],
    expected_error: str,
) -> None:
    config = AgentConfig(
        cmd="claude",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    )

    evidence = smoke_plumbing_module._subagent_smoke_evidence(config, lines)

    assert smoke_plumbing_module._subagent_smoke_error(evidence) == expected_error


def test_subagent_smoke_evidence_rejects_duplicate_dispatches() -> None:
    config = AgentConfig(
        cmd="claude",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    )
    lines = [
        '{"type":"assistant","message":{"content":['
        '{"type":"tool_use","id":"toolu_first","name":"Agent","input":{}},'
        '{"type":"tool_use","id":"toolu_second","name":"Task","input":{}}]}}',
        '{"type":"user","message":{"content":['
        '{"type":"tool_result","tool_use_id":"toolu_first","content":"done"}]}}',
        '{"type":"assistant","message":{"content":['
        '{"type":"tool_use","id":"toolu_write","name":"Write","input":{}}]}}',
    ]

    evidence = smoke_plumbing_module._subagent_smoke_evidence(config, lines)

    assert smoke_plumbing_module._subagent_smoke_error(evidence) == (
        "expected exactly one subagent dispatch, observed 2"
    )


def test_subagent_smoke_evidence_collapses_streamed_opencode_task() -> None:
    """A single OpenCode ``task`` streamed as running THEN completed is ONE dispatch.

    OpenCode may emit an intermediate (non-completed) tool state before the
    terminal one, and for a completed tool the parser now surfaces both the
    dispatch and the result. Counting raw ``tool_use`` events would see the same
    ``callID`` twice and reject a genuine single subagent with "expected exactly
    one subagent dispatch, observed 2". Dispatches are counted by distinct call
    ID, so a streamed call collapses to one.
    """
    config = AgentConfig(
        cmd="opencode",
        json_parser=JsonParserType.OPENCODE,
        transport=AgentTransport.OPENCODE,
    )
    running = (
        '{"type":"tool_use","part":{"type":"tool","tool":"task","callID":"call_1",'
        '"state":{"status":"running","input":{"description":"d"}}}}'
    )
    completed = (
        '{"type":"tool_use","part":{"type":"tool","tool":"task","callID":"call_1",'
        '"state":{"status":"completed","input":{"description":"d"},"output":"done"}}}'
    )
    post = '{"type":"text","part":{"type":"text","text":"the subagent finished"}}'

    evidence = smoke_plumbing_module._subagent_smoke_evidence(config, [running, completed, post])

    assert evidence.dispatch_count == 1, (
        f"a single streamed task must count as one dispatch; got {evidence.dispatch_count}"
    )
    assert smoke_plumbing_module._subagent_smoke_error(evidence) is None, (
        "a streamed single subagent with result and post-activity must pass"
    )


def test_subagent_smoke_evidence_rejects_mismatched_result_id() -> None:
    config = AgentConfig(
        cmd="claude",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    )
    lines = [
        '{"type":"assistant","message":{"content":['
        '{"type":"tool_use","id":"toolu_subagent","name":"Agent","input":{}}]}}',
        '{"type":"user","message":{"content":['
        '{"type":"tool_result","tool_use_id":"toolu_other","content":"done"}]}}',
        '{"type":"assistant","message":{"content":['
        '{"type":"tool_use","id":"toolu_write","name":"Write","input":{}}]}}',
    ]

    evidence = smoke_plumbing_module._subagent_smoke_evidence(config, lines)

    assert smoke_plumbing_module._subagent_smoke_error(evidence) == (
        "subagent result was not observed"
    )


def test_subagent_smoke_evidence_correlates_cursor_call_ids() -> None:
    config = AgentConfig(
        cmd="agent",
        json_parser=JsonParserType.GENERIC,
        transport=AgentTransport.CURSOR,
    )
    lines = [
        '{"type":"tool_call","subtype":"started","call_id":"subagent-1",'
        '"toolName":"Task","args":{}}',
        '{"type":"tool_call","subtype":"completed","call_id":"subagent-other",'
        '"toolName":"Task","result":"done"}',
        '{"type":"tool_call","subtype":"started","call_id":"write-1","toolName":"Write","args":{}}',
    ]

    evidence = smoke_plumbing_module._subagent_smoke_evidence(config, lines)

    assert smoke_plumbing_module._subagent_smoke_error(evidence) == (
        "subagent result was not observed"
    )


def test_detect_smoke_errors_enforces_subagent_evidence_only_for_requested_scenario(
    tmp_path: Path,
) -> None:
    config = AgentConfig(
        cmd="claude",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    )
    common = {
        "agent_name": "claude/haiku",
        "config": config,
        "unified_config": UnifiedConfig(),
        "workspace_root": tmp_path,
        "prompt_file": Path("PROMPT.md"),
        "output_file": tmp_path / "tmp" / "todo-list.js",
        "options": InvokeOptions(show_progress=False),
        "display_context": make_display_context(),
    }
    basic_params = SmokeRunParams(**common)
    subagent_params = SmokeRunParams(**common, subagents_requested=True)

    basic_errors = smoke_plumbing_module._detect_smoke_errors(basic_params, [], [], None, None)
    subagent_errors = smoke_plumbing_module._detect_smoke_errors(
        subagent_params, [], [], None, None
    )

    assert "subagent dispatch was not observed" not in basic_errors
    assert "subagent dispatch was not observed" in subagent_errors


def test_resolve_smoke_harness_spec_agy_uses_agy_layout() -> None:
    spec = smoke_plumbing_module.resolve_smoke_harness_spec("agy/Claude Sonnet 4.6 (Thinking)")
    assert spec.relative_dir == Path("tmp/interactive-agy-smoke")
    assert spec.output_file == Path("tmp/interactive-agy-smoke/todo-list.js")
    assert spec.run_id == "interactive-agy-smoke-Claude-Sonnet-4.6-Thinking"


def test_resolve_smoke_harness_spec_nanocoder_uses_nanocoder_layout() -> None:
    spec = smoke_plumbing_module.resolve_smoke_harness_spec("nanocoder")
    assert spec.relative_dir == Path("tmp/interactive-nanocoder-smoke")
    assert spec.output_file == Path("tmp/interactive-nanocoder-smoke/todo-list.js")
    assert spec.run_id == "interactive-nanocoder-smoke"


def test_resolve_smoke_harness_spec_nanocoder_alias_uses_unique_run_id() -> None:
    spec = smoke_plumbing_module.resolve_smoke_harness_spec("nanocoder/minimax/MiniMax-M3")
    assert spec.relative_dir == Path("tmp/interactive-nanocoder-smoke")
    assert spec.output_file == Path("tmp/interactive-nanocoder-smoke/todo-list.js")
    assert spec.run_id == "interactive-nanocoder-smoke-minimax-MiniMax-M3"


def test_resolve_smoke_harness_spec_cursor_bare_uses_cursor_layout() -> None:
    """Bare ``cursor`` resolves to the shared cursor harness layout.

    Pins AC-26: ``ralph smoke-interactive-cursor --agent cursor`` must NOT
    raise ``ValueError: No smoke harness spec defined for agent 'cursor'``
    from ``resolve_smoke_harness_spec``. Bare ``cursor`` uses the base
    cursor harness layout (no per-alias run_id suffix) so on-disk artifacts
    stay co-located with the shared output directory.
    """
    spec = smoke_plumbing_module.resolve_smoke_harness_spec("cursor")
    assert spec.relative_dir == Path("tmp/interactive-cursor-smoke")
    assert spec.output_file == Path("tmp/interactive-cursor-smoke/todo-list.js")
    assert spec.run_id == "interactive-cursor-smoke"


def test_resolve_smoke_harness_spec_cursor_alias_uses_unique_run_id() -> None:
    """``cursor/<model>`` resolves to a sanitized unique run_id.

    Pins AC-26: a smoke run with a non-default model alias (e.g.
    ``cursor/auto`` or ``cursor/gpt-5.3-codex-high``) MUST NOT collide on
    the completion-sentinel / receipt paths of the base cursor run. The
    suffix is sanitized the same way the agy/nanocoder branches sanitize it
    (run-id-safe characters only).
    """
    spec = smoke_plumbing_module.resolve_smoke_harness_spec("cursor/auto")
    assert spec.relative_dir == Path("tmp/interactive-cursor-smoke")
    assert spec.output_file == Path("tmp/interactive-cursor-smoke/todo-list.js")
    assert spec.run_id == "interactive-cursor-smoke-auto"

    # Bracket-parameterized model ids sanitize the brackets to a single dash.
    spec_bracket = smoke_plumbing_module.resolve_smoke_harness_spec(
        "cursor/claude-opus-4-8[context=1m]"
    )
    assert spec_bracket.run_id == "interactive-cursor-smoke-claude-opus-4-8-context-1m"


def test_run_smoke_plumbing_forwards_agent_name_to_harness_spec(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured_run_ids: list[str] = []
    captured_bridge_run_ids: list[str | None] = []
    cleared_run_ids: list[str] = []

    def fake_execute_agent_effect(*_args: object, **kwargs: object) -> PipelineEvent:
        run_id = kwargs.get("run_id")
        if isinstance(run_id, str):
            captured_run_ids.append(run_id)
        return PipelineEvent.AGENT_SUCCESS

    def fake_bridge_factory(**kwargs: object) -> object:
        run_id = kwargs.get("run_id")
        captured_bridge_run_ids.append(run_id if isinstance(run_id, str) else None)
        return _fake_bridge_factory(**kwargs)

    monkeypatch.setattr(
        smoke_plumbing_module,
        "AgentRegistry",
        _make_fake_registry(agent_name="agy/Claude Sonnet 4.6 (Thinking)"),
    )
    monkeypatch.setattr(
        smoke_plumbing_module,
        "execute_agent_effect",
        fake_execute_agent_effect,
    )
    monkeypatch.setattr(
        smoke_plumbing_module,
        "_clear_session_completion_sentinel",
        lambda _workspace_root, run_id: cleared_run_ids.append(run_id),
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
        agent_name="agy/Claude Sonnet 4.6 (Thinking)",
        prompt_file=tmp_path / "PROMPT.md",
        output_file=output_path,
        display_context=make_display_context(),
        pipeline_deps=PipelineDeps(
            display_context=make_display_context(),
            bridge_factory=fake_bridge_factory,
        ),
    )

    assert result.agent_name == "agy/Claude Sonnet 4.6 (Thinking)"
    assert cleared_run_ids == ["interactive-agy-smoke-Claude-Sonnet-4.6-Thinking"]
    assert captured_run_ids == ["interactive-agy-smoke-Claude-Sonnet-4.6-Thinking"]
    assert captured_bridge_run_ids == ["interactive-agy-smoke-Claude-Sonnet-4.6-Thinking"]


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


def _fake_execute_agent_effect_for_config(
    agent_name: str = "agy/Claude Sonnet 4.6 (Thinking)",
    *,
    raw_lines: tuple[str, ...] = (),
) -> Callable[..., PipelineEvent]:
    def fake_execute_agent_effect(*_args: object, **kwargs: object) -> PipelineEvent:
        raw_sink = kwargs.get("raw_output_sink")
        rendered_sink = kwargs.get("rendered_output_sink")
        output_relpath = (
            "tmp/interactive-agy-smoke/todo-list.js"
            if agent_name.startswith("agy/")
            else "tmp/interactive-claude-smoke/todo-list.js"
        )
        workspace_root = kwargs.get("workspace_root")
        if workspace_root is None:
            # Fallback: the SmokeRunParams are passed positionally after the effect.
            params = _args[2] if len(_args) >= 3 else None
            if isinstance(params, SmokeRunParams):
                workspace_root = params.workspace_root
        if isinstance(workspace_root, Path):
            output_path = workspace_root / output_relpath
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("export const todos = [];\n", encoding="utf-8")
            artifact_dir = workspace_root / ".agent" / "artifacts"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            (artifact_dir / "smoke_test_result.json").write_text(
                json.dumps(
                    {
                        "name": "smoke_test_result",
                        "artifact_type": "smoke_test_result",
                        "content": {
                            "status": "passed",
                            "summary": "ok",
                            "output_file": output_relpath,
                            "observed_working": ["tmp artifact created"],
                            "observed_breaks": [],
                            "headless_guide_checks": ["tool activity"],
                        },
                    }
                ),
                encoding="utf-8",
            )
        if raw_sink is not None:
            cast("deque[str]", raw_sink).extend(raw_lines)
            cast("deque[str]", raw_sink).append(
                "Task declared complete: session_id=dummy, summary=done\n"
                if not agent_name.startswith("agy/")
                else "agy planning line\n"
            )
        if rendered_sink is not None:
            cast("deque[str]", rendered_sink).append(
                "Task declared complete\n"
                if not agent_name.startswith("agy/")
                else "agy planning line\n"
            )
        return PipelineEvent.AGENT_SUCCESS

    return fake_execute_agent_effect


@pytest.mark.parametrize(
    ("transcript", "expected_error"),
    [
        (
            (
                '{"type":"assistant","message":{"content":['
                '{"type":"tool_use","id":"toolu_subagent","name":"Agent",'
                '"input":{}}]}}',
                '{"type":"user","message":{"content":['
                '{"type":"tool_result","tool_use_id":"toolu_subagent",'
                '"content":"done"}]}}',
                '{"type":"assistant","message":{"content":['
                '{"type":"tool_use","id":"toolu_write","name":"Write",'
                '"input":{}}]}}',
            ),
            None,
        ),
        (
            (
                '{"type":"assistant","message":{"content":['
                '{"type":"tool_use","id":"toolu_subagent","name":"Agent",'
                '"input":{}}]}}',
                '{"type":"user","message":{"content":['
                '{"type":"tool_result","tool_use_id":"toolu_other",'
                '"content":"done"}]}}',
            ),
            "subagent result was not observed",
        ),
        (
            (
                '{"type":"assistant","message":{"content":['
                '{"type":"tool_use","id":"toolu_subagent","name":"Agent",'
                '"input":{}}]}}',
                '{"type":"user","message":{"content":['
                '{"type":"tool_result","tool_use_id":"toolu_subagent",'
                '"content":"done"}]}}',
            ),
            "no meaningful activity was observed after the subagent result",
        ),
        (
            (
                '{"type":"assistant","message":{"content":['
                '{"type":"tool_use","id":"toolu_subagent","name":"Agent",'
                '"input":{}}]}}',
                '{"type":"user","message":{"content":['
                '{"type":"tool_result","tool_use_id":"toolu_subagent",'
                '"content":"done"}]}}',
                '{"type":"user","message":{"content":['
                '{"type":"tool_result","tool_use_id":"toolu_subagent",'
                '"content":"done again"}]}}',
            ),
            "no meaningful activity was observed after the subagent result",
        ),
        (
            (
                '{"type":"assistant","message":{"content":['
                '{"type":"tool_use","id":"toolu_first","name":"Agent","input":{}},'
                '{"type":"tool_use","id":"toolu_second","name":"Task","input":{}}]}}',
                '{"type":"user","message":{"content":['
                '{"type":"tool_result","tool_use_id":"toolu_first",'
                '"content":"done"}]}}',
                '{"type":"assistant","message":{"content":['
                '{"type":"tool_use","id":"toolu_write","name":"Write",'
                '"input":{}}]}}',
            ),
            "expected exactly one subagent dispatch, observed 2",
        ),
    ],
)
def test_run_smoke_plumbing_enforces_ordered_subagent_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    transcript: tuple[str, ...],
    expected_error: str | None,
) -> None:
    monkeypatch.setattr(
        smoke_plumbing_module,
        "AgentRegistry",
        _make_fake_registry(agent_name="claude/haiku"),
    )
    monkeypatch.setattr(
        smoke_plumbing_module,
        "execute_agent_effect",
        _fake_execute_agent_effect_for_config("claude/haiku", raw_lines=transcript),
    )

    result = smoke_plumbing_module.run_smoke_plumbing(
        config=UnifiedConfig(),
        workspace_root=tmp_path,
        agent_name="claude/haiku",
        prompt_file=tmp_path / "PROMPT.md",
        display_context=make_display_context(),
        pipeline_deps=PipelineDeps(
            display_context=make_display_context(),
            bridge_factory=_fake_bridge_factory,
        ),
        subagents=True,
    )

    lifecycle_errors = {
        "subagent dispatch was not observed",
        "subagent result was not observed",
        "no meaningful activity was observed after the subagent result",
        "expected exactly one subagent dispatch, observed 2",
    }
    observed_lifecycle_errors = lifecycle_errors.intersection(result.errors)
    assert observed_lifecycle_errors == ({expected_error} if expected_error else set())


def test_detect_break_indicators_uses_anchored_crash_patterns() -> None:
    """Incidental words like 'crash' in prose must not flag the detector."""
    assert smoke_plumbing_module._detect_break_indicators(["this should not crash"]) == []
    assert (
        "crash-like transcript output observed"
        in smoke_plumbing_module._detect_break_indicators(["Traceback (most recent call last):"])
    )
    assert (
        "crash-like transcript output observed"
        in smoke_plumbing_module._detect_break_indicators(["fatal: not a git repository"])
    )
    assert (
        "crash-like transcript output observed"
        in smoke_plumbing_module._detect_break_indicators(
            ["thread main panicked at src/main.rs:42"]
        )
    )
    assert (
        "crash-like transcript output observed"
        in smoke_plumbing_module._detect_break_indicators(["segmentation fault (core dumped)"])
    )


def test_agent_session_ceilings_agy_gets_360s_claude_gets_120s(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """AGY uses a 360s session ceiling; Claude keeps the legacy 120s ceiling."""
    captured_params: list[SmokeRunParams] = []

    def fake_run_smoke_agent(
        params: SmokeRunParams,
        run_id: str = "ignored",
    ) -> smoke_plumbing_module.SmokeRunResult:
        del run_id
        captured_params.append(params)
        return smoke_plumbing_module.SmokeRunResult(
            agent_name=params.agent_name,
            transport=params.config.transport.value,
            output_file=params.output_file,
            file_created=True,
            session_id=None,
            explicit_completion_seen=False,
            raw_line_count=0,
            parsed_event_count=0,
            tool_activity_seen=False,
            artifact_submitted=False,
            meaningful_output_lines=[],
            errors=[],
        )

    monkeypatch.setattr(
        smoke_plumbing_module,
        "_run_smoke_agent",
        fake_run_smoke_agent,
    )
    monkeypatch.setattr(
        smoke_plumbing_module,
        "AgentRegistry",
        _make_fake_registry(agent_name="agy/Claude Sonnet 4.6 (Thinking)"),
    )

    smoke_plumbing_module.run_smoke_plumbing(
        config=UnifiedConfig(),
        workspace_root=tmp_path,
        agent_name="agy/Claude Sonnet 4.6 (Thinking)",
        prompt_file=tmp_path / "PROMPT.md",
        display_context=make_display_context(),
        pipeline_deps=PipelineDeps(
            display_context=make_display_context(),
            bridge_factory=_fake_bridge_factory,
        ),
    )

    assert len(captured_params) == 1
    assert captured_params[0].unified_config.general.agent_max_session_seconds == 360.0

    captured_params.clear()
    monkeypatch.setattr(
        smoke_plumbing_module,
        "AgentRegistry",
        _make_fake_registry(agent_name="claude/haiku"),
    )

    smoke_plumbing_module.run_smoke_plumbing(
        config=UnifiedConfig(),
        workspace_root=tmp_path,
        agent_name="claude/haiku",
        prompt_file=tmp_path / "PROMPT.md",
        display_context=make_display_context(),
        pipeline_deps=PipelineDeps(
            display_context=make_display_context(),
            bridge_factory=_fake_bridge_factory,
        ),
    )

    assert len(captured_params) == 1
    assert captured_params[0].unified_config.general.agent_max_session_seconds == 120.0


def _make_artifact(
    tmp_path: Path,
    *,
    observed_breaks: list[str],
    run_id: str = "interactive-claude-smoke",
) -> None:
    artifact_path = tmp_path / ".agent" / "tmp" / "smoke_test_result.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    breaks_section = (
        "\n## Observed Breaks\n"
        + "\n".join(
            f"- [BR-{index}] {item}" for index, item in enumerate(observed_breaks, 1)
        )
        if observed_breaks
        else ""
    )
    artifact_path.write_text(
        f"""\
---
type: smoke_test_result
status: passed
output_file: tmp/interactive-agy-smoke/todo-list.js
---
## Summary
- [SUM-1] Smoke checks passed.

## Observed Working
- [OK-1] tmp artifact created

{breaks_section}
## Headless Guide Checks
- [HG-1] tool activity
""",
        encoding="utf-8",
    )
    result = promote_fallback_artifact(
        tmp_path,
        "smoke_test_result",
        run_id=run_id,
    )
    assert result is not None


def test_detect_smoke_errors_agy_without_artifact_reports_missing_completion(
    tmp_path: Path,
) -> None:
    """AGY with no artifact write still fails the completion check.

    The completion signal for AGY is the canonical receipt promoted from the
    agent's direct artifact write (see
    ``smoke_plumbing._explicit_completion_seen`` for the AGY branch and
    the regression test
    ``test_agy_smoke_completion_requires_receipt_not_transcript_marker`` in
    tests/test_smoke_plumbing_uses_canonical_submit.py). When the agent
    never writes the artifact, no receipt is promoted, and the smoke run
    must fail with ``"smoke_test_result artifact was not submitted"`` —
    not with the legacy ``"declare_complete marker was not observed"``
    message, which was removed because the substring check was spoofable.
    """
    output_file = tmp_path / "tmp" / "interactive-agy-smoke" / "todo-list.js"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("export const todos = [];\n", encoding="utf-8")
    # CRUCIALLY: do NOT write the artifact. The smoke run must report the
    # missing-receipt completion failure, not the legacy transcript-marker
    # failure.
    artifact_path = tmp_path / ".agent" / "artifacts" / "smoke_test_result.json"
    if artifact_path.exists():
        artifact_path.unlink()

    config = AgentConfig(
        cmd="agy",
        json_parser=JsonParserType.GENERIC,
        transport=AgentTransport.AGY,
    )
    params = SmokeRunParams(
        agent_name="agy/Claude Sonnet 4.6 (Thinking)",
        config=config,
        unified_config=UnifiedConfig(),
        workspace_root=tmp_path,
        prompt_file=Path("PROMPT.md"),
        output_file=output_file,
        options=InvokeOptions(show_progress=False),
        display_context=make_display_context(),
        bridge=None,
    )

    errors = smoke_plumbing_module._detect_smoke_errors(params, [], [], None, None)

    # The new contract: AGY completion requires the canonical receipt
    # (promoted from the artifact write). With no artifact, the
    # completion check fails; the user-facing failure wording is
    # transport-agnostic on purpose, so we assert that the failure
    # surfaces as EITHER the receipt-missing failure (artifact not
    # submitted) or the completion-marker failure (declare_complete
    # marker was not observed) since both are user-visible signals of
    # the same underlying gap. The important invariant is that the
    # smoke run does NOT silently pass: a transcript substring cannot
    # satisfy the AGY completion check (see the companion regression
    # test ``test_agy_smoke_completion_requires_receipt_not_transcript_marker``
    # in tests/test_smoke_plumbing_uses_canonical_submit.py for the
    # strict contract).
    assert (
        "smoke_test_result artifact was not submitted" in errors
        or "declare_complete marker was not observed" in errors
    ), f"Expected a completion-failure error, got: {errors}"
    # The transcript-only marker path must NOT satisfy completion.
    # Drive the harness with a transcript that contains the substring
    # and confirm the failure still fires (i.e. the substring is not
    # trusted as a completion signal for AGY).
    transcript_with_marker = ["I will create the todo list.", "Task declared complete:"]
    errors_with_marker = smoke_plumbing_module._detect_smoke_errors(
        params, transcript_with_marker, transcript_with_marker, None, None
    )
    assert "declare_complete marker was not observed" in errors_with_marker, (
        "AGY completion must NOT be satisfied by the transcript substring "
        "'Task declared complete:'. The substring is a spoofable signal and "
        "the new contract requires the canonical receipt. "
        f"Got errors: {errors_with_marker}"
    )
    assert "smoke_test_result artifact was not submitted" in errors_with_marker, (
        "With no artifact and no receipt, AGY completion must fail for "
        f"missing receipt. Got errors: {errors_with_marker}"
    )
    assert "session ID was not observed" not in errors


def test_detect_smoke_errors_agy_self_reported_tool_activity_does_not_count(
    tmp_path: Path,
) -> None:
    """AGY tool activity must come from authoritative sources, not the artifact.

    Replaces the prior test that pinned the removed self-certifying
    contract (artifact ``headless_guide_checks`` declaring
    ``"tool activity"`` was treated as proof of tool activity). The
    authoritative sources for AGY are: (a) a ``[plain] tool: NAME`` parser
    event in the transcript, or (b) the expected workspace file
    ``tmp/interactive-agy-smoke/todo-list.js`` being written. The
    artifact's self-report is NEVER trusted; see the regression test
    ``test_agy_tool_activity_must_not_come_from_artifact`` in
    ``tests/test_smoke_plumbing_uses_canonical_submit.py`` for the full
    contract.
    """
    output_file = tmp_path / "tmp" / "interactive-agy-smoke" / "todo-list.js"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    # CRUCIALLY: do NOT write the workspace file. The file write is the
    # authoritative AGY tool-activity signal, so a pre-created file would
    # mask the regression.
    artifact_path = tmp_path / ".agent" / "artifacts" / "smoke_test_result.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    # Self-reporting artifact: the headless_guide_checks declare "tool
    # activity" but no actual tool activity is observed (no parser event,
    # no file write). The harness must NOT trust the self-report.
    artifact_path.write_text(
        json.dumps(
            {
                "name": "smoke_test_result",
                "artifact_type": "smoke_test_result",
                "content": {
                    "status": "passed",
                    "summary": "ok",
                    "output_file": "tmp/interactive-agy-smoke/todo-list.js",
                    "observed_working": ["tmp artifact created"],
                    "observed_breaks": [],
                    "headless_guide_checks": ["tool activity"],
                },
            }
        ),
        encoding="utf-8",
    )

    config = AgentConfig(
        cmd="agy",
        json_parser=JsonParserType.GENERIC,
        transport=AgentTransport.AGY,
    )
    params = SmokeRunParams(
        agent_name="agy/Claude Sonnet 4.6 (Thinking)",
        config=config,
        unified_config=UnifiedConfig(),
        workspace_root=tmp_path,
        prompt_file=Path("PROMPT.md"),
        output_file=output_file,
        options=InvokeOptions(show_progress=False),
        display_context=make_display_context(),
        bridge=None,
    )

    # Empty transcript: no [plain] tool: events for the parser to classify.
    errors = smoke_plumbing_module._detect_smoke_errors(params, [], [], None, None)

    assert "no tool activity was observed" in errors
    assert "expected todo-list.js was not created" in errors


def test_detect_smoke_errors_agy_artifact_with_breaks_satisfies_completion(
    tmp_path: Path,
) -> None:
    """AGY with a canonical receipt satisfies completion even with breaks.

    The completion signal for AGY is the canonical receipt promoted from
    the agent's direct artifact write, independent of the
    ``observed_breaks`` field. When the receipt is present, the completion
    check passes; breaks are reported in the ``Observed breaks`` section
    but do not block completion. The legacy test asserted the opposite
    (breaks block completion) because the old substring check was both
    spoofable and conflated completion with breaks.
    """
    output_file = tmp_path / "tmp" / "interactive-agy-smoke" / "todo-list.js"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("export const todos = [];\n", encoding="utf-8")
    _make_artifact(
        tmp_path,
        observed_breaks=["something went wrong"],
        run_id="interactive-claude-smoke",
    )

    config = AgentConfig(
        cmd="agy",
        json_parser=JsonParserType.GENERIC,
        transport=AgentTransport.AGY,
    )
    params = SmokeRunParams(
        agent_name="agy/Claude Sonnet 4.6 (Thinking)",
        config=config,
        unified_config=UnifiedConfig(),
        workspace_root=tmp_path,
        prompt_file=Path("PROMPT.md"),
        output_file=output_file,
        options=InvokeOptions(show_progress=False),
        display_context=make_display_context(),
        bridge=None,
    )

    # Pre-compute the receipt status the way ``_run_smoke_agent`` would:
    # call ``_is_smoke_artifact_submitted`` to promote the fallback artifact
    # to a canonical receipt, mirroring the live runtime.
    artifact_submitted = smoke_plumbing_module._is_smoke_artifact_submitted(params.workspace_root)
    assert artifact_submitted is True, (
        "Test setup invariant: the artifact must be promoted to a receipt"
    )

    errors = smoke_plumbing_module._detect_smoke_errors(
        params, [], [], None, None, artifact_submitted=artifact_submitted
    )

    # The receipt is present, so completion is satisfied; neither the
    # transcript-marker failure nor the artifact-not-submitted failure
    # should fire.
    assert "declare_complete marker was not observed" not in errors
    assert "smoke_test_result artifact was not submitted" not in errors
    assert "session ID was not observed" not in errors


def test_detect_smoke_errors_nanocoder_receipt_satisfies_completion_and_tool_activity(
    tmp_path: Path,
) -> None:
    """Nanocoder interactive completion is proven by the smoke artifact receipt."""
    output_file = tmp_path / "tmp" / "interactive-nanocoder-smoke" / "todo-list.js"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("export const todos = [];\n", encoding="utf-8")
    _make_artifact(
        tmp_path,
        observed_breaks=[],
        run_id="interactive-nanocoder-smoke",
    )

    config = AgentConfig(
        cmd="nanocoder",
        json_parser=JsonParserType.GENERIC,
        transport=AgentTransport.NANOCODER,
    )
    params = SmokeRunParams(
        agent_name="nanocoder",
        config=config,
        unified_config=UnifiedConfig(),
        workspace_root=tmp_path,
        prompt_file=Path("PROMPT.md"),
        output_file=output_file,
        options=InvokeOptions(show_progress=False),
        display_context=make_display_context(),
        bridge=None,
    )
    run_id = "interactive-nanocoder-smoke"

    artifact_submitted = smoke_plumbing_module._is_smoke_artifact_submitted(
        params.workspace_root,
        run_id,
    )
    assert artifact_submitted is True, (
        "Test setup invariant: the Nanocoder artifact must be promoted to the smoke run receipt"
    )

    errors = smoke_plumbing_module._detect_smoke_errors(
        params,
        [],
        [],
        None,
        None,
        artifact_submitted=artifact_submitted,
        run_id=run_id,
    )

    assert "declare_complete marker was not observed" not in errors
    assert "no tool activity was observed" not in errors
    assert "smoke_test_result artifact was not submitted" not in errors
    assert "session ID was not observed" not in errors


def test_detect_smoke_errors_nanocoder_banner_without_progress_reports_prompt_submission_failure(
    tmp_path: Path,
) -> None:
    """Nanocoder's welcome banner alone must not look like successful startup."""
    output_file = tmp_path / "tmp" / "interactive-nanocoder-smoke" / "todo-list.js"
    config = AgentConfig(
        cmd="nanocoder",
        json_parser=JsonParserType.GENERIC,
        transport=AgentTransport.NANOCODER,
    )
    params = SmokeRunParams(
        agent_name="nanocoder/MiniMax Coding/MiniMax-M3",
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
        "█▄ █ ▄▀█ █▄ █ █▀█ █▀▀ █▀█ █▀▄ █▀▀ █▀█",
        "█ ▀█ █▀█ █ ▀█ █▄█ █▄▄ █▄█ █▄▀ ██▄ █▀▄",
        "✻ Welcome to Nanocoder 1.28.1 ✻",
        "│  Tips for getting started:",
        "│  1. Use natural language to describe your task.",
        "│  2. Ask for file analysis, editing, bash commands and more.",
    ]

    errors = smoke_plumbing_module._detect_smoke_errors(
        params,
        lines,
        lines,
        None,
        None,
        artifact_submitted=False,
        run_id="interactive-nanocoder-smoke-MiniMax-Coding-MiniMax-M3",
    )

    assert "nanocoder prompt was not submitted after startup banner" in errors


def test_detect_smoke_errors_non_agy_transport_keeps_missing_signal_checks(
    tmp_path: Path,
) -> None:
    """The declare_complete and session ID gates are per-agent, not global."""
    output_file = tmp_path / "tmp" / "interactive-claude-smoke" / "todo-list.js"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("export const todos = [];\n", encoding="utf-8")
    _make_artifact(tmp_path, observed_breaks=[])

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

    errors = smoke_plumbing_module._detect_smoke_errors(params, [], [], None, None)

    assert "declare_complete marker was not observed" in errors
    assert "session ID was not observed" in errors


def _make_agy_params(tmp_path: Path) -> SmokeRunParams:
    output_file = tmp_path / "tmp" / "interactive-agy-smoke" / "todo-list.js"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    config = AgentConfig(
        cmd="agy",
        json_parser=JsonParserType.GENERIC,
        transport=AgentTransport.AGY,
    )
    return SmokeRunParams(
        agent_name="agy/Claude Sonnet 4.6 (Thinking)",
        config=config,
        unified_config=UnifiedConfig(),
        workspace_root=tmp_path,
        prompt_file=Path("PROMPT.md"),
        output_file=output_file,
        options=InvokeOptions(show_progress=False),
        display_context=make_display_context(),
        bridge=None,
    )


def test_detect_smoke_errors_agy_empty_output_reports_quota_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When AGY emits no stdout and the CLI log shows quota exhaustion, the
    smoke report includes an actionable upstream diagnostic."""
    log_path = tmp_path / "cli.log"
    log_path.write_text(
        "...\n"
        "agent executor error: RESOURCE_EXHAUSTED (code 429): "
        "Individual quota reached. Contact your administrator...\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(smoke_plumbing_module, "_AGY_CLI_LOG_PATH", log_path)

    errors = smoke_plumbing_module._detect_smoke_errors(
        _make_agy_params(tmp_path), [], [], None, None
    )

    assert any("quota exhausted" in err.lower() for err in errors), errors


def test_detect_smoke_errors_agy_empty_output_includes_quota_reset_window(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When the CLI log contains a 'Resets in ...' window, the diagnostic
    includes it so the operator knows how long to wait."""
    log_path = tmp_path / "cli.log"
    log_path.write_text(
        "...\n"
        "agent executor error: RESOURCE_EXHAUSTED (code 429): "
        "Individual quota reached. Resets in 2h54m12s.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(smoke_plumbing_module, "_AGY_CLI_LOG_PATH", log_path)

    errors = smoke_plumbing_module._detect_smoke_errors(
        _make_agy_params(tmp_path), [], [], None, None
    )

    assert any("resets in 2h54m12s" in err.lower() for err in errors), errors


def test_detect_smoke_errors_agy_empty_output_reports_model_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When AGY emits no stdout and the CLI log shows an unknown model ID, the
    smoke report names the rejected model."""
    log_path = tmp_path / "cli.log"
    log_path.write_text(
        "...\n"
        "model_resolver.go:62] Resolving model gemini-3.5-flash-low\n"
        "model_config_manager.go:54] Failed to resolve model flag "
        "gemini-3.5-flash-low: model gemini-3.5-flash-low is not recognized "
        "as a known model or custom model in settings\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(smoke_plumbing_module, "_AGY_CLI_LOG_PATH", log_path)

    errors = smoke_plumbing_module._detect_smoke_errors(
        _make_agy_params(tmp_path), [], [], None, None
    )

    assert any("gemini-3.5-flash-low" in err and "not recognized" in err for err in errors), errors


def test_detect_smoke_errors_agy_empty_output_reports_generic_diagnostic_when_log_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When AGY emits no stdout and no CLI log is present, a generic pointer
    to the CLI log is still reported."""
    missing_log = tmp_path / "no_such_cli.log"
    monkeypatch.setattr(smoke_plumbing_module, "_AGY_CLI_LOG_PATH", missing_log)

    errors = smoke_plumbing_module._detect_smoke_errors(
        _make_agy_params(tmp_path), [], [], None, None
    )

    assert any("AGY --print returned empty stdout" in err and "cli.log" in err for err in errors), (
        errors
    )


def test_detect_smoke_errors_agy_no_diagnostic_when_stdout_present(
    tmp_path: Path,
) -> None:
    """The upstream diagnostic is only added when AGY produced zero stdout."""
    errors = smoke_plumbing_module._detect_smoke_errors(
        _make_agy_params(tmp_path),
        ["some stdout line\n"],
        [],
        None,
        None,
    )

    assert not any("AGY --print returned empty stdout" in err for err in errors)


def test_read_smoke_test_result_artifact_returns_none_for_invalid_content(
    tmp_path: Path,
) -> None:
    """An artifact with invalid content (missing required field) returns None."""
    artifact_dir = tmp_path / ".agent" / "artifacts"
    artifact_path = artifact_dir / "smoke_test_result.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        "---\ntype: smoke_test_result\nstatus: passed\n---\n",
        encoding="utf-8",
    )

    result = read_smoke_test_result_artifact(tmp_path)
    assert result is None


def test_read_smoke_test_result_artifact_returns_none_for_missing_file(
    tmp_path: Path,
) -> None:
    """A missing artifact file returns None."""

    result = read_smoke_test_result_artifact(tmp_path)
    assert result is None


def test_read_smoke_test_result_artifact_returns_validated_content(
    tmp_path: Path,
) -> None:
    """A fully valid artifact returns the validated content dict."""
    artifact_dir = tmp_path / ".agent" / "artifacts"
    artifact_path = artifact_dir / "smoke_test_result.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        """\
---
type: smoke_test_result
status: passed
output_file: tmp/smoke/output.js
---
## Summary
- [SUM-1] all checks passed

## Observed Working
- [OK-1] created output

## Headless Guide Checks
- [HG-1] tool activity
""",
        encoding="utf-8",
    )

    result = read_smoke_test_result_artifact(tmp_path)
    assert result is not None
    assert result["status"] == "passed"
    assert result["summary"] == "all checks passed"


def test_detect_smoke_errors_agy_no_diagnostic_when_artifact_present(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The upstream diagnostic is not added when AGY wrote the artifact file."""
    _make_artifact(tmp_path, observed_breaks=[])
    log_path = tmp_path / "cli.log"
    log_path.write_text("RESOURCE_EXHAUSTED (code 429)\n", encoding="utf-8")
    monkeypatch.setattr(smoke_plumbing_module, "_AGY_CLI_LOG_PATH", log_path)

    errors = smoke_plumbing_module._detect_smoke_errors(
        _make_agy_params(tmp_path), [], [], None, None
    )

    assert not any("AGY --print returned empty stdout" in err for err in errors)
