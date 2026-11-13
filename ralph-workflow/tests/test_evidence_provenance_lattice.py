from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.completion_signals import _check_completion_sentinel, is_artifact_submitted
from ralph.agents.invoke import InvokeOptions
from ralph.cli.commands.smoke import _required_evidence
from ralph.config.enums import AgentTransport
from ralph.config.mcp_models import McpConfig
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.mcp.artifacts.smoke_test_result import SMOKE_TEST_RESULT_ARTIFACT_TYPE
from ralph.mcp.protocol._session_drain import SessionDrain
from ralph.mcp.protocol.capability_mapping import Capability
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._in_memory_transport import drive_request, parse_sse_data
from ralph.mcp.server._wire_ledger import append_wire_record
from ralph.mcp.server.runtime import McpServer
from ralph.mcp.tools.bridge import build_ralph_tool_registry
from ralph.mcp.tools.coordination import handle_declare_complete
from ralph.mcp.tools.md_artifact import handle_submit_md_artifact
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.plumbing import smoke_plumbing
from ralph.pipeline.plumbing.smoke_evidence import (
    DEGRADED,
    PASS,
    Evidence,
    Provenance,
    absent,
    format_verdict,
    grade_verdict,
)
from ralph.pipeline.plumbing.smoke_plumbing import (
    SmokeRunParams,
    SmokeRunResult,
    _artifact_submission_evidence,
    _completion_evidence,
    _run_smoke_agent,
    _tool_activity_evidence,
    transport_evidence_ceiling,
)
from ralph.workspace.fs import FsWorkspace
from tests._artifact_format_docs_mock_workspace import MockWorkspace
from tests._support.mock_agy import (
    DEGRADED_BASELINE_RUN_ID,
    degraded_baseline_artifact_markdown,
    degraded_baseline_stream_json_lines,
)

if TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch


class _SmokePipelineDepsStub:
    recovery_clock = None
    policy_bundle = None
    ledger = None
    unavailability_store = None


def test_provenance_ordering_is_least_to_most_trustworthy() -> None:
    assert (
        Provenance.ABSENT
        < Provenance.HOST_SYNTHESIZED
        < Provenance.WORKSPACE_EFFECT
        < Provenance.TRANSCRIPT
        < Provenance.WIRE
    )


def test_evidence_cannot_hold_true_with_absent_provenance() -> None:
    with pytest.raises(ValueError, match=r"Provenance\.ABSENT"):
        Evidence(holds=True, provenance=Provenance.ABSENT, detail="bogus")


def test_evidence_requires_a_provenance_member() -> None:
    non_provenance_value: object = "WIRE"
    with pytest.raises(TypeError):
        Evidence(holds=True, provenance=non_provenance_value, detail="bogus")


def test_absent_helper_returns_canonical_non_holding_evidence() -> None:
    ev = absent("nothing to see here")
    assert ev.holds is False
    assert ev.provenance is Provenance.ABSENT
    assert ev.detail == "nothing to see here"


def test_grade_verdict_requires_every_fact_at_wire_for_pass() -> None:
    all_wire = {
        "a": Evidence(True, Provenance.WIRE, "x"),
        "b": Evidence(True, Provenance.WIRE, "y"),
    }
    label, weakest = grade_verdict(all_wire)
    assert label == PASS
    assert weakest == Provenance.WIRE


def test_grade_verdict_demotes_to_degraded_when_any_fact_below_wire() -> None:
    mixed = {
        "a": Evidence(True, Provenance.WIRE, "x"),
        "b": Evidence(True, Provenance.TRANSCRIPT, "y"),
    }
    label, weakest = grade_verdict(mixed)
    assert label == DEGRADED
    assert weakest == Provenance.TRANSCRIPT


def test_grade_verdict_reports_the_single_weakest_provenance_overall() -> None:
    facts = {
        "a": Evidence(True, Provenance.WORKSPACE_EFFECT, "x"),
        "b": Evidence(True, Provenance.TRANSCRIPT, "y"),
        "c": Evidence(True, Provenance.HOST_SYNTHESIZED, "z"),
    }
    label, weakest = grade_verdict(facts)
    assert label == DEGRADED
    assert weakest == Provenance.HOST_SYNTHESIZED


def test_grade_verdict_never_passes_when_a_fact_does_not_hold() -> None:
    facts = {
        "a": Evidence(True, Provenance.WIRE, "x"),
        "b": absent("missing"),
    }
    label, weakest = grade_verdict(facts)
    assert label == DEGRADED
    assert weakest == Provenance.ABSENT


def test_grade_verdict_empty_mapping_grades_degraded_absent() -> None:
    label, weakest = grade_verdict({})
    assert label == DEGRADED
    assert weakest == Provenance.ABSENT


def test_format_verdict_pass_has_no_parenthetical() -> None:
    assert format_verdict({"a": Evidence(True, Provenance.WIRE, "x")}) == "PASS"


def test_format_verdict_degraded_names_the_weakest_provenance() -> None:
    facts = {"a": Evidence(True, Provenance.HOST_SYNTHESIZED, "x")}
    assert format_verdict(facts) == "DEGRADED (host-synthesized)"


def _agy_config() -> AgentConfig:
    return AgentConfig(cmd="agy", transport=AgentTransport.AGY)


def _init_frame(tool_names: list[str]) -> str:
    return json.dumps(
        {
            "event": "init",
            "conversation_id": "00000000-0000-0000-0000-000000000001",
            "init": {
                "cwd": "/workspace",
                "tools": tool_names,
                "permission_mode": "always-proceed",
            },
        }
    )


def test_ceiling_reports_below_wire_when_no_ralph_tool_advertised() -> None:
    tool_names = [
        "ask_permission",
        "ask_question",
        "define_subagent",
        "invoke_subagent",
        "manage_subagents",
        "view_file",
        "write_to_file",
        "grep_search",
        "run_command",
    ]
    lines = [_init_frame(tool_names)]

    ceiling = transport_evidence_ceiling(_agy_config(), lines)

    assert ceiling < Provenance.WIRE
    assert ceiling == Provenance.TRANSCRIPT


def test_ceiling_reports_wire_when_a_ralph_tool_is_advertised() -> None:
    lines = [_init_frame(["view_file", "ralph_submit_md_artifact", "write_to_file"])]

    assert transport_evidence_ceiling(_agy_config(), lines) == Provenance.WIRE


def test_ceiling_reports_wire_when_call_mcp_tool_dispatcher_is_advertised() -> None:
    lines = [_init_frame(["view_file", "call_mcp_tool", "write_to_file"])]

    assert transport_evidence_ceiling(_agy_config(), lines) == Provenance.WIRE


def test_ceiling_reports_absent_when_no_init_frame_present() -> None:
    lines = [json.dumps({"event": "step_update", "step_update": {"step_index": 0}})]

    assert transport_evidence_ceiling(_agy_config(), lines) == Provenance.ABSENT


def test_ceiling_ignores_non_json_and_malformed_lines() -> None:
    lines = [
        "plain text banner",
        "{not valid json",
        _init_frame(["view_file"]),
    ]

    assert transport_evidence_ceiling(_agy_config(), lines) == Provenance.TRANSCRIPT


def _opencode_config() -> AgentConfig:
    return AgentConfig(cmd="opencode", transport=AgentTransport.OPENCODE)


def _opencode_tool_use_frame(tool_name: str) -> str:
    return json.dumps(
        {
            "type": "tool_use",
            "timestamp": 1785000003000,
            "sessionID": "s_0001",
            "part": {
                "type": "tool",
                "tool": tool_name,
                "callID": "c_0002",
                "state": {"status": "completed", "input": {}},
                "id": "p_0003",
                "sessionID": "s_0001",
                "messageID": "m_0002",
            },
        }
    )


def test_ceiling_reports_wire_when_opencode_tool_use_names_a_ralph_tool() -> None:
    lines = [_opencode_tool_use_frame("ralph_read_file")]

    assert transport_evidence_ceiling(_opencode_config(), lines) == Provenance.WIRE


def test_ceiling_reports_transcript_when_opencode_tool_use_names_a_non_ralph_tool() -> None:
    lines = [_opencode_tool_use_frame("read")]

    assert transport_evidence_ceiling(_opencode_config(), lines) == Provenance.TRANSCRIPT


def test_ceiling_stays_absent_for_opencode_lifecycle_frames_with_no_tool_use() -> None:
    step_start = json.dumps(
        {
            "type": "step_start",
            "timestamp": 1785000001000,
            "sessionID": "s_0001",
            "part": {
                "id": "p_0001",
                "messageID": "m_0001",
                "sessionID": "s_0001",
                "type": "step-start",
            },
        }
    )

    assert transport_evidence_ceiling(_opencode_config(), [step_start]) == Provenance.ABSENT


def test_ceiling_prefers_init_frame_signal_over_opencode_tool_use_fallback() -> None:
    lines = [_init_frame(["view_file"]), _opencode_tool_use_frame("ralph_read_file")]

    assert transport_evidence_ceiling(_agy_config(), lines) == Provenance.TRANSCRIPT


def test_2026_08_05_run_grades_degraded() -> None:
    required_facts = {
        "artifact_submitted": Evidence(
            holds=True,
            provenance=Provenance.WORKSPACE_EFFECT,
            detail="promoted the fallback file .agent/tmp/smoke_test_result.md",
        ),
        "tool_activity_seen": Evidence(
            holds=True,
            provenance=Provenance.TRANSCRIPT,
            detail="14 frames, 0 tools/call",
        ),
        "explicit_completion_seen": Evidence(
            holds=False,
            provenance=Provenance.ABSENT,
            detail="completion sentinel was not observed",
        ),
    }

    label, weakest = grade_verdict(required_facts)

    assert label == DEGRADED
    assert weakest == Provenance.ABSENT
    assert format_verdict(required_facts) == "DEGRADED (absent)"
    assert label != PASS, "the 2026-08-05 run must never grade PASS or print 'Breaks: none'"


def test_evidence_ceiling_reported_while_still_streaming(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    real_report = smoke_plumbing._report_evidence_ceiling_once

    def _tracking_report(config: AgentConfig, lines: list[str]) -> bool:
        calls.append(list(lines))
        return real_report(config, lines)

    monkeypatch.setattr(smoke_plumbing, "_report_evidence_ceiling_once", _tracking_report)

    config = _agy_config()
    output_dir = tmp_path / "tmp" / "interactive-agy-smoke"
    output_dir.mkdir(parents=True)
    output_file = output_dir / "todo-list.js"
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("smoke prompt", encoding="utf-8")
    params = SmokeRunParams(
        agent_name="agy/gemini-3.6-flash-low",
        config=config,
        unified_config=UnifiedConfig(general=GeneralConfig()),
        workspace_root=tmp_path,
        prompt_file=prompt_file,
        output_file=output_file,
        options=InvokeOptions(),
        display_context=make_display_context(),
        bridge=object(),
        pipeline_deps=_SmokePipelineDepsStub(),
    )
    run_id = "raw-line-sink-mid-stream-run"

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        del args
        raw_sink = kwargs.get("raw_output_sink")
        raw_line_sink = kwargs.get("raw_line_sink")
        assert callable(raw_line_sink), (
            "S-1 requires execute_agent_effect to accept raw_line_sink and "
            "smoke_plumbing to pass a real observer through it"
        )

        init_line = _init_frame(["view_file", "call_mcp_tool", "write_to_file"])
        if isinstance(raw_sink, deque):
            raw_sink.append(init_line)
        raw_line_sink(init_line)

        assert len(calls) == 1, "ceiling must be reported synchronously as the init line streams in"
        assert calls[0] == [init_line]

        later_line = json.dumps(
            {"event": "step_update", "step_update": {"step_type": "agent_response"}}
        )
        if isinstance(raw_sink, deque):
            raw_sink.append(later_line)
        raw_line_sink(later_line)
        assert len(calls) == 1, "ceiling must be reported exactly once per run"

        output_file.write_text("// smoke output\n", encoding="utf-8")
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    result = _run_smoke_agent(params, run_id=run_id)

    assert len(calls) == 1
    assert result.transport_evidence_ceiling == Provenance.WIRE


def _all_capabilities() -> set[str]:
    return {cap.value for cap in Capability}


def test_transcript_replay_with_real_mcp_dispatch_grades_wire_pass(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    test_secret = "s3-wire-dispatch-secret"
    monkeypatch.setenv("RALPH_BROKER_SECRET", test_secret)

    config = _agy_config()
    output_dir = tmp_path / "tmp" / "interactive-agy-smoke"
    output_dir.mkdir(parents=True)
    output_file = output_dir / "todo-list.js"
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("smoke prompt", encoding="utf-8")
    params = SmokeRunParams(
        agent_name="agy/gemini-3.6-flash-low",
        config=config,
        unified_config=UnifiedConfig(general=GeneralConfig()),
        workspace_root=tmp_path,
        prompt_file=prompt_file,
        output_file=output_file,
        options=InvokeOptions(),
        display_context=make_display_context(),
        bridge=object(),
        pipeline_deps=_SmokePipelineDepsStub(),
    )
    run_id = "wire-dispatch-run"

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        raw_sink = kwargs.get("raw_output_sink")
        if isinstance(raw_sink, deque):
            raw_sink.append(_init_frame(["view_file", "call_mcp_tool", "write_to_file"]))

        session = AgentSession(
            session_id="wire-dispatch-session",
            run_id=run_id,
            drain=SessionDrain.DEVELOPMENT.value,
            capabilities=_all_capabilities(),
            broker_secret=test_secret,
        )
        workspace = FsWorkspace(params.workspace_root)
        registry = build_ralph_tool_registry(session, workspace, mcp_config=McpConfig())
        server = McpServer(session, workspace, registry)

        submit_payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "ralph_submit_md_artifact",
                    "arguments": {
                        "artifact_type": "smoke_test_result",
                        "content": degraded_baseline_artifact_markdown(),
                    },
                },
            }
        ).encode()
        _status, _headers, body = drive_request(server, submit_payload)
        submit_response = parse_sse_data(body)
        assert "error" not in submit_response, (
            f"ralph_submit_md_artifact tools/call returned an error: {submit_response}"
        )

        complete_payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "declare_complete",
                    "arguments": {"summary": "S-3 Part A wire-dispatch proof"},
                },
            }
        ).encode()
        _status, _headers, body = drive_request(server, complete_payload)
        complete_response = parse_sse_data(body)
        assert "error" not in complete_response, (
            f"declare_complete tools/call returned an error: {complete_response}"
        )

        output_file.write_text("// smoke output\n", encoding="utf-8")
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    result = _run_smoke_agent(params, run_id=run_id)

    assert result.transport_evidence_ceiling == Provenance.WIRE

    assert result.artifact_submitted.holds is True
    assert result.artifact_submitted.provenance == Provenance.WIRE
    assert result.explicit_completion_seen.holds is True
    assert result.explicit_completion_seen.provenance == Provenance.WIRE
    assert result.tool_activity_seen.holds is True
    assert result.tool_activity_seen.provenance == Provenance.WIRE

    required_facts = _required_evidence(result)
    label, weakest = grade_verdict(required_facts)

    assert label == PASS
    assert weakest == Provenance.WIRE


class _WireCorrelationSession:
    def __init__(self, run_id: str, broker_secret: str | None) -> None:
        self.session_id = "wire-correlation-session"
        self.run_id = run_id
        self.drain = "development"
        self.broker_secret = broker_secret

    def check_capability(self, capability: str) -> object:
        del capability
        return "approved"


def test_grading_functions_recognize_wire_evidence_from_existing_ledger_rows(
    tmp_path: Path,
) -> None:
    test_secret = "s3-part-b-secret"
    run_id = "wire-correlation-run"
    workspace = MockWorkspace(tmp_path)
    session = _WireCorrelationSession(run_id=run_id, broker_secret=test_secret)

    handle_submit_md_artifact(
        session,
        workspace,
        {
            "artifact_type": SMOKE_TEST_RESULT_ARTIFACT_TYPE,
            "content": degraded_baseline_artifact_markdown(),
        },
    )
    handle_declare_complete(session, workspace, {"summary": "part B correlation"})

    append_wire_record(
        tmp_path,
        method="tools/call",
        tool_name="ralph_submit_md_artifact",
        params={"artifact_type": "smoke_test_result"},
        run_id=run_id,
        secret=test_secret,
    )
    append_wire_record(
        tmp_path,
        method="tools/call",
        tool_name="declare_complete",
        params={"summary": "part B correlation"},
        run_id=run_id,
        secret=test_secret,
    )

    artifact_submitted = is_artifact_submitted(
        tmp_path, run_id, SMOKE_TEST_RESULT_ARTIFACT_TYPE, receipt_secret=test_secret
    )
    completion_present = _check_completion_sentinel(tmp_path, run_id, sentinel_secret=test_secret)
    assert artifact_submitted is True
    assert completion_present is True

    artifact_evidence = _artifact_submission_evidence(
        tmp_path, run_id, submitted=artifact_submitted, secret=test_secret
    )
    completion_evidence = _completion_evidence(
        tmp_path,
        run_id,
        present=completion_present,
        host_synthesized=False,
        secret=test_secret,
    )

    assert artifact_evidence.provenance == Provenance.WIRE
    assert completion_evidence.provenance == Provenance.WIRE

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
        pipeline_deps=_SmokePipelineDepsStub(),
    )
    tool_activity_evidence = _tool_activity_evidence(
        params,
        [],
        run_id=run_id,
        secret=test_secret,
        tool_activity_holds=False,
    )
    assert tool_activity_evidence.provenance == Provenance.WIRE


def test_2026_08_05_transcript_replay_grades_degraded_host_synthesized(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.delenv("RALPH_BROKER_SECRET", raising=False)

    config = _agy_config()
    output_dir = tmp_path / "tmp" / "interactive-agy-smoke"
    output_dir.mkdir(parents=True)
    output_file = output_dir / "todo-list.js"
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("smoke prompt", encoding="utf-8")
    params = SmokeRunParams(
        agent_name="agy/gemini-3.6-flash-low",
        config=config,
        unified_config=UnifiedConfig(general=GeneralConfig()),
        workspace_root=tmp_path,
        prompt_file=prompt_file,
        output_file=output_file,
        options=InvokeOptions(),
        display_context=make_display_context(),
        bridge=object(),
        pipeline_deps=_SmokePipelineDepsStub(),
    )
    run_id = DEGRADED_BASELINE_RUN_ID
    artifact_path = tmp_path / ".agent" / "tmp" / "smoke_test_result.md"

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        raw_sink = kwargs.get("raw_output_sink")
        if isinstance(raw_sink, deque):
            raw_sink.extend(degraded_baseline_stream_json_lines())
        output_file.write_text("// smoke output\n", encoding="utf-8")
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(degraded_baseline_artifact_markdown(), encoding="utf-8")
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    result = _run_smoke_agent(params, run_id=run_id)

    assert result.transport_evidence_ceiling == Provenance.TRANSCRIPT

    assert result.artifact_submitted.holds is True
    assert result.artifact_submitted.provenance == Provenance.WORKSPACE_EFFECT
    assert result.explicit_completion_seen.holds is False
    assert result.explicit_completion_seen.provenance == Provenance.ABSENT
    assert result.tool_activity_seen.holds is True
    assert result.tool_activity_seen.provenance == Provenance.TRANSCRIPT

    required_facts = _required_evidence(result)
    label, weakest = grade_verdict(required_facts)

    assert label == DEGRADED
    assert weakest == Provenance.ABSENT
    assert format_verdict(required_facts) == "DEGRADED (absent)"
    assert label != PASS, "the 2026-08-05 run must never grade PASS or print 'Breaks: none'"


def _wire_run_result(
    *,
    multimodal_requested: bool,
    multimodal_tool_used: Evidence | None,
) -> SmokeRunResult:
    output = Path("tmp/interactive-agy-smoke/todo-list.js")
    return SmokeRunResult(
        agent_name="agy/gemini-3.6-flash-low",
        transport="agy",
        output_file=output,
        file_created=True,
        session_id="2f50d6ef-a009-427f-99e8-c58ac99c1f8d",
        explicit_completion_seen=Evidence(True, Provenance.WIRE, "declare_complete wire match"),
        raw_line_count=16,
        parsed_event_count=19,
        tool_activity_seen=Evidence(True, Provenance.WIRE, "tools/call ledger match"),
        artifact_submitted=Evidence(
            True, Provenance.WIRE, "receipt matched a tools/call ledger record"
        ),
        meaningful_output_lines=[],
        errors=[],
        multimodal_requested=multimodal_requested,
        multimodal_tool_used=multimodal_tool_used,
    )


def test_required_evidence_carries_multimodal_tool_used_when_requested_but_ungraded() -> None:
    result = _wire_run_result(
        multimodal_requested=True,
        multimodal_tool_used=None,
    )

    required = _required_evidence(result)

    assert "multimodal_tool_used" in required, (
        "_required_evidence dropped multimodal_tool_used for a "
        "multimodal_requested run; criterion 5 forbids the silent downgrade"
    )


def test_required_evidence_demotes_run_when_multimodal_fact_ungraded() -> None:
    result = _wire_run_result(
        multimodal_requested=True,
        multimodal_tool_used=None,
    )

    label, weakest = grade_verdict(_required_evidence(result))

    assert label != PASS, (
        "a multimodal_requested run whose multimodal_tool_used was never "
        "graded must NOT grade PASS; got PASS (silent downgrade, criterion 5)"
    )
    assert weakest is Provenance.ABSENT


def test_required_evidence_non_multimodal_run_has_only_three_canonical_keys() -> None:
    result = _wire_run_result(
        multimodal_requested=False,
        multimodal_tool_used=None,
    )

    required = _required_evidence(result)

    assert set(required) == {"artifact_submitted", "explicit_completion_seen", "tool_activity_seen"}
    assert label_for(result) == PASS


def _claude_config() -> AgentConfig:
    return AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE)


def test_ceiling_reports_wire_for_claude_system_init_frame() -> None:
    frame = json.dumps(
        {
            "type": "system",
            "subtype": "init",
            "tools": ["mcp__ralph__write_file", "mcp__ralph__read_file"],
            "mcp_servers": [{"name": "ralph", "status": "connected"}],
        }
    )
    assert transport_evidence_ceiling(_claude_config(), [frame]) == Provenance.WIRE


def test_ceiling_latches_wire_even_when_init_frame_evicted_from_transcript_fifo(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("test prompt", encoding="utf-8")
    output_file = tmp_path / "output.txt"

    params = SmokeRunParams(
        agent_name="claude-headless/haiku",
        config=_claude_config(),
        unified_config=UnifiedConfig(general=GeneralConfig()),
        workspace_root=tmp_path,
        prompt_file=prompt_file,
        output_file=output_file,
        options=InvokeOptions(),
        display_context=make_display_context(),
        bridge=object(),
        pipeline_deps=_SmokePipelineDepsStub(),
    )
    run_id = "test-latched-ceiling-evicted"

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        del args
        raw_sink = kwargs.get("raw_output_sink")
        raw_line_sink = kwargs.get("raw_line_sink")
        assert callable(raw_line_sink)

        init_line = json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "tools": ["mcp__ralph__write_file"],
                "mcp_servers": [{"name": "ralph"}],
            }
        )
        if isinstance(raw_sink, deque):
            raw_sink.append(init_line)
        raw_line_sink(init_line)

        for i in range(450):
            line = json.dumps({"event": "step_update", "step_index": i})
            if isinstance(raw_sink, deque):
                raw_sink.append(line)
            raw_line_sink(line)

        output_file.write_text("// smoke output\n", encoding="utf-8")
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    result = _run_smoke_agent(params, run_id=run_id)

    assert result.transport_evidence_ceiling == Provenance.WIRE


def label_for(result: SmokeRunResult) -> str:
    return grade_verdict(_required_evidence(result))[0]
