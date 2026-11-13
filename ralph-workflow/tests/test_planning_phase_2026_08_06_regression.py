from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.completion_signals import (
    CompletionSignals,
    format_phase_verdict,
    graded_verdict,
)
from ralph.agents.invoke import InvokeOptions
from ralph.agents.parsers.agy import AgyParser
from ralph.config.enums import AgentTransport, Verbosity
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.phases.required_artifacts import RequiredArtifact
from ralph.pipeline import phase_agent_handler as phase_agent_handler_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.plumbing.smoke_evidence import Provenance
from ralph.pipeline.plumbing.smoke_plumbing import (
    SmokeRunParams,
    _run_smoke_agent,
)
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Iterator

    import pytest


def _agy_config() -> AgentConfig:
    return AgentConfig(
        cmd="agy",
        json_parser="generic",
        transport=AgentTransport.AGY,
    )


def _2026_08_06_init_frame() -> str:
    return json.dumps(
        {
            "event": "init",
            "conversation_id": "00000000-0000-0000-0000-000000000000",
            "init": {
                "model": "gemini-3.6-flash-low",
                "cwd": ".",
                "tools": [
                    "ask_permission",
                    "call_mcp_tool",
                    "grep_search",
                    "list_dir",
                    "read_resource",
                    "run_command",
                    "view_file",
                    "write_to_file",
                ],
                "permission_mode": "always-proceed",
            },
        }
    )


def _2026_08_06_step_update_lines() -> Iterator[str]:
    base = {
        "conversation_id": "00000000-0000-0000-0000-000000000000",
    }
    step_types = [
        ("user_input", 0, None, None, None),
        ("agent_response", 1, None, None, None),
        ("tool", 2, "view_file", {"AbsolutePath": ".agent/PROMPT.md"}, None),
        ("agent_response", 3, None, None, None),
        ("tool", 4, "view_file", {"AbsolutePath": ".agent/PRODUCT_CRITERIA.md"}, None),
        ("agent_response", 5, None, None, None),
        ("tool", 6, "grep_search", {"query": "PROMPT.md", "path": "."}, None),
        ("agent_response", 7, None, None, None),
        ("tool", 8, "list_dir", {"AbsolutePath": "."}, None),
        ("agent_response", 9, None, None, None),
        ("tool", 10, "view_file", {"AbsolutePath": ".agent/PLAN.md"}, None),
        ("agent_response", 11, None, None, None),
        ("tool", 12, "view_file", {"AbsolutePath": ".agent/artifacts/plan.md"}, None),
        ("agent_response", 13, None, None, None),
        ("tool", 14, "grep_search", {"query": "plan", "path": "ralph"}, None),
        ("agent_response", 15, None, None, None),
        ("tool", 16, "view_file", {"AbsolutePath": ".agent/PROMPT.md"}, None),
        ("agent_response", 17, None, None, None),
        ("tool", 18, "list_dir", {"AbsolutePath": "ralph/prompts"}, None),
        ("agent_response", 19, None, None, None),
        ("tool", 20, "run_command", {"command": "make test"}, "exit code: 0"),
        ("agent_response", 21, None, None, None),
    ]
    for step_type, idx, tool_name, params, output in step_types:
        step_update: dict[str, object] = {
            **base,
            "step_index": idx,
            "state": "DONE",
            "step_type": step_type,
            "duration_seconds": 0.5,
        }
        if tool_name is not None:
            step_update["tool_name"] = tool_name
            step_update["tool_info"] = {
                "name": tool_name,
                "parameters": params,
            }
            if output is not None:
                step_update["tool_info"]["output"] = output
        yield json.dumps({"event": "step_update", "step_update": step_update})


def _agy_result_success_frame() -> str:
    return json.dumps(
        {
            "event": "result",
            "result": {
                "status": "SUCCESS",
                "duration_seconds": 3.58,
                "num_turns": 1,
            },
        }
    )


def test_2026_08_06_planning_run_phases_as_failure() -> None:
    lines = [_2026_08_06_init_frame()]
    lines.extend(_2026_08_06_step_update_lines())
    lines.append(_agy_result_success_frame())

    parser = AgyParser()
    events = list(parser.parse(iter(lines)))

    stop_events = [e for e in events if e.type == "stop"]
    assert stop_events, "AGY parser must emit a stop event for the result frame"
    assert stop_events[0].metadata.get("_transcript_claimed_outcome") is True
    assert "agy result SUCCESS" in stop_events[0].content

    signals = CompletionSignals(
        explicit_complete=False,
        required_artifact_present=False,
        artifact_types=(),
        completion_sentinel_present=False,
        artifact_required=True,
        unsubmitted_draft_present=False,
    )
    label, weakest = graded_verdict(signals)
    assert label == "DEGRADED"
    assert weakest == Provenance.ABSENT
    assert format_phase_verdict(signals) == "DEGRADED (absent)"

    assert "absent" in format_phase_verdict(signals).lower()


def test_2026_08_06_planning_run_through_smoke_agent_grades_degraded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections import deque

    class _StubSmokePipelineDeps:
        recovery_clock = None
        policy_bundle = None
        ledger = None
        unavailability_store = None

    params = SmokeRunParams(
        agent_name="agy/gemini-3.6-flash-low",
        config=_agy_config(),
        unified_config=UnifiedConfig(general=GeneralConfig()),
        workspace_root=tmp_path,
        prompt_file=tmp_path / "PROMPT.md",
        output_file=tmp_path / "tmp" / "interactive-agy-smoke" / "todo-list.js",
        options=InvokeOptions(),
        display_context=make_display_context(),
        bridge=object(),
        pipeline_deps=_StubSmokePipelineDeps(),
    )
    run_id = "interactive-agy-smoke-test-model"

    transcript_lines = [_2026_08_06_init_frame()]
    transcript_lines.extend(_2026_08_06_step_update_lines())
    transcript_lines.append(_agy_result_success_frame())

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        raw_sink = kwargs.get("raw_output_sink")
        if isinstance(raw_sink, deque):
            raw_sink.extend(transcript_lines)
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    result = _run_smoke_agent(params, run_id=run_id)

    assert result.artifact_submitted.holds is False
    assert result.explicit_completion_seen.holds is False
    assert (
        "smoke_test_result artifact was not submitted" in result.errors
        or ("no tool activity was observed" in result.errors)
        or (result.tool_activity_seen.holds is True)
    )


def test_2026_08_06_planning_run_through_real_phase_verdict_path_grades_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    required_artifact = RequiredArtifact(
        phase="plan",
        artifact_type="plan",
        artifact_path=".agent/artifacts/plan.md",
        markdown_path=None,
        normalizer=None,
        artifact_required=True,
    )

    class _StubPolicyBundle:
        pipeline = object()
        artifacts = object()

    captured: list[tuple[str, tuple[object, ...]]] = []

    def _fake_emit_via_display(
        ctx: object, method_name: str, *args: object, **kwargs: object
    ) -> bool:
        del ctx, kwargs
        captured.append((method_name, args))
        return True

    monkeypatch.setattr(
        phase_agent_handler_module,
        "resolve_phase_required_artifact",
        lambda *args, **kwargs: required_artifact,
    )
    monkeypatch.setattr(phase_agent_handler_module, "_emit_via_display", _fake_emit_via_display)

    effect = InvokeAgentEffect(
        agent_name="agy/gemini-3.6-flash-low",
        phase="plan",
        prompt_file="planning_prompt.md",
        drain="plan",
    )
    workspace = FsWorkspace(tmp_path)

    phase_agent_handler_module.render_phase_failure_report(
        effect,
        policy_bundle=_StubPolicyBundle(),
        workspace=workspace,
        display=None,
        display_context=make_display_context(),
        verbosity=Verbosity.VERBOSE,
        run_id="interactive-agy-2026-08-06-planning-run",
    )

    assert len(captured) == 1, f"expected exactly one rendered verdict line, got {captured}"
    method_name, args = captured[0]
    assert method_name == "emit"
    assert args[0] == "agy/gemini-3.6-flash-low"
    message = str(args[1])

    assert "FAILED (no artifact)" in message, message
    assert "plan" in message, message
    assert "SUCCESS" not in message, message
