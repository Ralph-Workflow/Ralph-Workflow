"""Kimi transport WIRE-provenance dispatch replay (DA-001 / DA-006 / DA-008).

Extracted from ``tests/test_evidence_provenance_lattice.py`` to keep that
module under the 1000-line repo-structure cap. Shares the same in-process
real-MCP-dispatch pattern as the lattice module's Part A test: role-keyed
Kimi stream-json frames, real ``McpServer.handle_request`` ``tools/call``
round trips, and the shared ``_run_smoke_agent`` grading path.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

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


def test_kimi_transcript_replay_with_real_mcp_dispatch_grades_wire_pass(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """DA-001/DA-006/DA-008: pin the WIRE path for the Kimi transport.

    The live Kimi smoke (2026-08-17) created the file, submitted the
    artifact, and declared completion, yet graded ``DEGRADED
    (workspace-effect)`` with all three required facts below WIRE: the
    smoke process carried no ``RALPH_BROKER_SECRET``, so every
    wire-ledger append was a documented no-op and the shared gate could
    never observe a wire-backed ``tools/call`` for the run. This test
    replays the Kimi-shaped run through the SAME real in-process MCP
    dispatch chain Part A pins for AGY -- role-keyed stream-json frames
    Kimi actually emits, real ``McpServer.handle_request`` ``tools/call``
    round trips for ``ralph_submit_md_artifact`` and
    ``declare_complete``, the shared ``_run_smoke_agent`` grading path --
    and proves the gate reaches ``PASS`` / WIRE when the session carries
    a broker secret, so the remaining live gap is the missing secret at
    the smoke composition root, not any Kimi transport/parser defect.
    """
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
            # The measured v0.36.1 role-keyed frame vocabulary: a version
            # banner, the session.resume_hint meta frame carrying the
            # resumable session id, and assistant text frames. The tool
            # activity itself is witnessed on the wire ledger below (the
            # real tools/call dispatches), not only in transcript text.
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
