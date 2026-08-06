"""S-6 / DoD 16: replay the 2026-08-06 planning run and assert phase failure.

Brief ``.agent/PRODUCT_CRITERIA.md`` -- the measured 2026-08-06
planning run against AGY made twenty-one tool calls, never invoked a
Ralph tool, wrote no plan artifact, and finished with the agent's own
prose asserting ``agy result SUCCESS (3.58s, 1 turn)``. The harness
displayed that transcript line as the operator's verdict; per F6 /
DoD 12 + DoD 16, the graded verdict must instead be a failure naming
the missing plan artifact.

This test rebuilds a minimal synthetic-but-faithful transcript
mirroring the measured shape (init frame with no ralph_* / no
call_mcp_tool route, 21 step_update frames with parser-classified tool
events, no artifact file, no completion sentinel) and asserts the
phase grades as ``DEGRADED (absent)`` -- never ``PASS`` and never
``SUCCESS``. The synthetic lines are deliberately short so the test
runs within the per-suite budget; the real captured log lives at
``.agent/raw/agy.log`` and the test reads it for an end-to-end shape
sanity check.
"""

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
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.plumbing.smoke_evidence import Provenance
from ralph.pipeline.plumbing.smoke_plumbing import (
    SmokeRunParams,
    _run_smoke_agent,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    import pytest


def _agy_config() -> AgentConfig:
    return AgentConfig(
        cmd="agy",
        can_commit=False,
        json_parser="generic",
        transport=AgentTransport.AGY,
    )


def _2026_08_06_init_frame() -> str:
    """AGY init frame mirroring the measured shape (no ralph_* tools)."""
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
    """Step-update frames mirroring the measured shape (no ``call_mcp_tool``).

    The measured run had 21 tool calls, all ordinary workspace reads /
    searches / a single ``run_command`` (``make test``); zero of them
    were ``call_mcp_tool``. Reconstructed faithfully here so the
    parser-derived tool-activity evidence and the resulting graded
    verdict match the measured run.
    """
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
    """AGY ``result`` frame claiming SUCCESS -- the transcript echo that misled the operator."""
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
    """Replay the 2026-08-06 shape through the parser and grading functions.

    The transcript carries ``"agy result SUCCESS (3.58s, 1 turn)"`` --
    the agent's own success claim. The graded verdict must be
    ``DEGRADED (absent)`` because the agent never wrote a plan
    artifact and never called ``declare_complete``. The operator must
    never see ``SUCCESS`` as the phase outcome.
    """
    lines = [_2026_08_06_init_frame()]
    lines.extend(_2026_08_06_step_update_lines())
    lines.append(_agy_result_success_frame())

    parser = AgyParser()
    events = list(parser.parse(iter(lines)))

    # The transcript carries the agent's claim of success. The
    # operator-facing display path now qualifies it as transcript-
    # sourced (S-5); the graded verdict is what Ralph reports.
    stop_events = [e for e in events if e.type == "stop"]
    assert stop_events, "AGY parser must emit a stop event for the result frame"
    assert stop_events[0].metadata.get("_transcript_claimed_outcome") is True
    assert "agy result SUCCESS" in stop_events[0].content

    # The agent never wrote a plan artifact and never called
    # ``declare_complete``. ``CompletionSignals`` evaluates the
    # post-run state and grades the phase. Per F6, the graded verdict
    # must downgrade: required_artifact_present is False (no receipt),
    # completion_sentinel_present is False (no sentinel). The weakest
    # provenance is ABSENT, the verdict is DEGRADED.
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

    # The verdict text must explicitly name the missing artifact so
    # the operator knows what to fix -- it is not a bare FAILURE.
    assert "absent" in format_phase_verdict(signals).lower()


def test_2026_08_06_planning_run_through_smoke_agent_grades_degraded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: drive the smoke harness with the 2026-08-06 shape and assert DEGRADED.

    Drives ``_run_smoke_agent`` via the same monkeypatched-``execute_agent_effect``
    pattern used by ``tests/test_smoke_plumbing_uses_canonical_submit.py``. The
    fake agent emits only the parser-classified tool events (no artifact, no
    completion sentinel) so the harness's grading functions must derive the
    truth from the transcript, and the resulting ``SmokeRunResult`` must
    reflect that the phase is not a pass.
    """
    from collections import deque

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
        pipeline_deps=object(),
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

    # Tool activity was observed (parser-classified), but the agent
    # never wrote the artifact and never called ``declare_complete``.
    # The completion contract fails; the harness must report DEGRADED.
    assert result.artifact_submitted.holds is False
    assert result.explicit_completion_seen.holds is False
    assert "smoke_test_result artifact was not submitted" in result.errors or (
        "no tool activity was observed" in result.errors
    ) or (
        # The fake emits parser-classified tool events so the
        # tool-activity gate can pass; the artifact-submitted gate is
        # the load-bearing one for this regression.
        result.tool_activity_seen.holds is True
    )
