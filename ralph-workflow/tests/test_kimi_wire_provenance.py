from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke import InvokeOptions
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
from tests.test_evidence_provenance_lattice import _all_capabilities

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
        pipeline_deps=object(),
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
