from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke import InvokeOptions
from ralph.agents.parsers.kimi import KimiParser
from ralph.cli.commands.smoke import _required_evidence
from ralph.config.enums import AgentTransport
from ralph.config.mcp_models import McpConfig
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.mcp.protocol._session_drain import SessionDrain
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._in_memory_transport import drive_request, parse_sse_data
from ralph.mcp.server.runtime import McpServer
from ralph.mcp.tools.bridge import build_ralph_tool_registry
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.plumbing.smoke_evidence import (
    PASS,
    Provenance,
    grade_verdict,
)
from ralph.pipeline.plumbing.smoke_plumbing import SmokeRunParams, _run_smoke_agent
from ralph.workspace.fs import FsWorkspace
from tests._support.mock_agy import degraded_baseline_artifact_markdown
from tests.test_evidence_provenance_lattice import _all_capabilities, _SmokePipelineDepsStub

if TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch


@pytest.mark.timeout_seconds(5)
def test_kimi_transcript_replay_with_real_mcp_dispatch_grades_wire_pass(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    test_secret = "kimi-wire-dispatch-secret"
    monkeypatch.setenv("RALPH_BROKER_SECRET", test_secret)

    config = AgentConfig(cmd="kimi", transport=AgentTransport.KIMI)
    output_dir = tmp_path / "tmp" / "interactive-kimi-smoke"
    output_dir.mkdir(parents=True)
    output_file = output_dir / "todo-list.js"
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("smoke prompt", encoding="utf-8")
    params = SmokeRunParams(
        agent_name="kimi/kimi-code/kimi-for-coding",
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
    run_id = "kimi-wire-dispatch-run"

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        raw_sink = kwargs.get("raw_output_sink")
        if isinstance(raw_sink, deque):
            raw_sink.extend(
                [
                    json.dumps({"role": "meta", "type": "system.version", "version": "0.36.1"}),
                    json.dumps(
                        {
                            "role": "meta",
                            "type": "session.resume_hint",
                            "session_id": "kimi-wire-session",
                            "command": "kimi -S kimi-wire-session",
                        }
                    ),
                    json.dumps({"role": "assistant", "content": "creating the todo list"}),
                    json.dumps(
                        {"role": "assistant", "content": "file written, submitting the artifact"}
                    ),
                    json.dumps(
                        {
                            "role": "assistant",
                            "content": "submission receipt valid; declaring completion",
                        }
                    ),
                ]
            )

        session = AgentSession(
            session_id="kimi-wire-session",
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
                    "arguments": {"summary": "DA-001 Kimi wire-dispatch proof"},
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

    assert result.session_id == "kimi-wire-session"
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


_FIXTURE_PATH: Path = Path(__file__).parent / "display" / "_fixtures" / "kimi_wire.jsonl"
_PROVENANCE_PATH: Path = Path(__file__).parent / "display" / "_fixtures" / "kimi_wire_provenance.md"


def test_kimi_wire_fixture_is_loadable_ndjson() -> None:
    assert _FIXTURE_PATH.is_file()
    lines = _FIXTURE_PATH.read_text(encoding="utf-8").splitlines()
    assert len(lines) > 0
    for line in lines:
        parsed_json = json.loads(line)
        assert isinstance(parsed_json, dict)


def test_kimi_wire_fixture_parses_expected_event_sequence() -> None:
    lines = _FIXTURE_PATH.read_text(encoding="utf-8").splitlines()
    parser = KimiParser()
    events = list(parser.parse(iter(lines)))

    assert len(events) == 7
    event_types = [event.type for event in events]
    assert event_types == [
        "lifecycle",
        "tool_use",
        "tool_result",
        "tool_use",
        "tool_result",
        "lifecycle",
        "text",
    ]

    tools_called = [event.metadata.get("tool") for event in events if event.type == "tool_use"]
    assert tools_called == ["Write", "Read"]

    lifecycle_events = [event for event in events if event.type == "lifecycle"]
    assert len(lifecycle_events) == 2
    assert "2.3.0" in lifecycle_events[0].content
    assert "session_eb6d7ec7-8341-4357-b73b-099e24b8102f" in lifecycle_events[1].content


def test_kimi_wire_provenance_document_matches_fixture() -> None:
    assert _PROVENANCE_PATH.is_file()
    text = _PROVENANCE_PATH.read_text(encoding="utf-8")
    assert "2.3.0" in text
    assert "stream-json" in text
    assert "kimi_wire.jsonl" in text
