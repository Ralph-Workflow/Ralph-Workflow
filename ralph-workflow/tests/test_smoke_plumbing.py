"""Tests for the smoke-test plumbing core.

These tests are black-box and use injected fakes only: no real subprocess,
no real network, no time.sleep, no real file I/O.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.idle_watchdog import WatchdogFireReason
from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    InactivityTimeoutOpts,
    InvokeOptions,
    OpenCodeResumableExitError,
)
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
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


def test_execute_smoke_turns_retries_post_tool_empty_response_with_same_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = AgentConfig(
        cmd="claude",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE,
    )
    bridge = _FakeBridge()
    params = SmokeRunParams(
        agent_name="claude",
        config=config,
        workspace_root=tmp_path,
        prompt_file=tmp_path / "PROMPT.md",
        output_file=tmp_path / "todo.js",
        options=InvokeOptions(show_progress=False),
        display_context=make_display_context(),
        bridge=bridge,
    )
    calls: list[object | None] = []

    failure = AgentInvocationError(
        "claude",
        1,
        "Model returned an empty response with no tool calls",
        parsed_output=[
            '{"type":"session","session_id":"sess-smoke"}',
            '{"type":"tool_result","tool":"read_file"}',
        ],
    )

    def fake_invoke_agent(
        _config: AgentConfig,
        _prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> object:
        calls.append(options.session_id if options is not None else None)
        if len(calls) == 1:
            raise failure
        return iter(
            [
                '{"type":"assistant","message":{"type":"message","content":'
                '[{"type":"text","text":"Recovered smoke run."}]}\n',
                "Claude session ready. Session ID: sess-smoke\n",
                "claude tool: read_file\n",
                "Task declared complete: session_id=sess-smoke, summary=done, timestamp=1\n",
            ]
        )

    monkeypatch.setattr(smoke_plumbing_module, "invoke_agent", fake_invoke_agent)
    lines, _rendered, session_id, final_exception = smoke_plumbing_module._execute_smoke_turns(
        params, None
    )

    assert final_exception is None
    assert session_id == "sess-smoke"
    assert calls == [None, "sess-smoke"]
    assert any('"type":"tool_result"' in line for line in lines)
    assert any("Recovered smoke run." in line for line in lines)


def test_execute_smoke_turns_preserves_early_session_id_across_long_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = AgentConfig(
        cmd="claude",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE,
    )
    bridge = _FakeBridge()
    params = SmokeRunParams(
        agent_name="claude",
        config=config,
        workspace_root=tmp_path,
        prompt_file=tmp_path / "PROMPT.md",
        output_file=tmp_path / "todo.js",
        options=InvokeOptions(show_progress=False),
        display_context=make_display_context(),
        bridge=bridge,
    )
    calls: list[object | None] = []

    def fake_invoke_agent(
        _config: AgentConfig,
        _prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> object:
        calls.append(options.session_id if options is not None else None)
        if len(calls) == 1:
            lines = ["Claude session ready. Session ID: sess-long\n"]
            lines.extend(f"line-{index}\n" for index in range(500))

            def _iter() -> object:
                yield from lines
                raise AgentInvocationError(
                    "claude",
                    1,
                    "Model returned an empty response with no tool calls",
                    parsed_output=['{"type":"tool_result","tool":"read_file"}'],
                )

            return _iter()
        return iter(
            [
                "Claude session ready. Session ID: sess-long\n",
                "Task declared complete: session_id=sess-long, summary=done, timestamp=1\n",
            ]
        )

    monkeypatch.setattr(smoke_plumbing_module, "invoke_agent", fake_invoke_agent)
    _lines, _rendered, session_id, final_exception = smoke_plumbing_module._execute_smoke_turns(
        params, None
    )

    assert final_exception is None
    assert session_id == "sess-long"
    assert calls == [None, "sess-long"]


def test_execute_smoke_turns_preserves_early_session_id_for_resumable_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = AgentConfig(
        cmd="claude",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE,
    )
    params = SmokeRunParams(
        agent_name="claude",
        config=config,
        workspace_root=tmp_path,
        prompt_file=tmp_path / "PROMPT.md",
        output_file=tmp_path / "todo.js",
        options=InvokeOptions(show_progress=False),
        display_context=make_display_context(),
        bridge=None,
    )
    calls: list[object | None] = []

    def fake_run_smoke_attempt(
        _params: object,
        options: InvokeOptions,
        *,
        session_id_sink: object = None,
    ) -> tuple[list[str], list[str]]:
        calls.append(options.session_id)
        assert callable(session_id_sink)
        session_id_sink("sess-resume")
        raise OpenCodeResumableExitError("claude", session_id=None)

    monkeypatch.setattr(smoke_plumbing_module, "_run_smoke_attempt", fake_run_smoke_attempt)
    _lines, _rendered, session_id, final_exception = smoke_plumbing_module._execute_smoke_turns(
        params, None
    )

    assert final_exception is not None
    assert session_id == "sess-resume"
    assert calls == [None, "sess-resume", "sess-resume", "sess-resume", "sess-resume"]


def test_run_smoke_attempt_preserves_inactivity_timeout_resume_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = AgentConfig(
        cmd="claude",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    )
    params = SmokeRunParams(
        agent_name="claude/haiku",
        config=config,
        workspace_root=tmp_path,
        prompt_file=tmp_path / "PROMPT.md",
        output_file=tmp_path / "todo.js",
        options=InvokeOptions(show_progress=False),
        display_context=make_display_context(),
        bridge=None,
    )

    def fake_invoke_agent(
        _config: AgentConfig,
        _prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> object:
        del _config, _prompt_file, options
        return iter(["Claude session ready. Session ID: sess-timeout\n"])

    def fake_stream_parsed_agent_activity(*args: object, **kwargs: object) -> None:
        raw_output_sink = kwargs.get("raw_output_sink")
        if hasattr(raw_output_sink, "append"):
            raw_output_sink.append("Claude session ready. Session ID: sess-timeout\n")
        raise AgentInactivityTimeoutError(
            "claude",
            30.0,
            ["claude tool: write_file"],
            InactivityTimeoutOpts(
                reason=WatchdogFireReason.NO_OUTPUT_DEADLINE,
                session_resume_safe=True,
                resumable_session_id="sess-timeout",
            ),
        )

    monkeypatch.setattr(smoke_plumbing_module, "invoke_agent", fake_invoke_agent)
    monkeypatch.setattr(
        smoke_plumbing_module,
        "stream_parsed_agent_activity",
        fake_stream_parsed_agent_activity,
    )

    with pytest.raises(AgentInactivityTimeoutError) as exc_info:
        smoke_plumbing_module._run_smoke_attempt(params, params.options)

    assert exc_info.value.session_resume_safe is True
    assert exc_info.value.resumable_session_id == "sess-timeout"
    assert any(
        "Claude session ready. Session ID: sess-timeout" in line
        for line in exc_info.value.parsed_output
    )


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
