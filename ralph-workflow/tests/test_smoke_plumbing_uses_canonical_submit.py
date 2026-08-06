"""Smoke plumbing must produce canonical receipts for Claude and AGY branches.

The smoke harness has two submission paths:

- Claude branch: the agent calls ``handle_submit_md_artifact``.
- AGY branch: the agent writes ``.agent/tmp/smoke_test_result.md`` directly
  because AGY headless mode does not reliably call Ralph's MCP tools.

Both paths must end with a run-scoped canonical receipt as artifact-persistence
evidence. Completion remains a separate durable ``declare_complete`` sentinel;
neither the receipt nor a transcript marker is sufficient on its own.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.completion_signals import is_artifact_submitted
from ralph.agents.invoke import InvokeOptions
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.mcp.artifacts.completion_receipts import artifact_receipt_present
from ralph.mcp.artifacts.smoke_test_result import (
    SMOKE_TEST_RESULT_ARTIFACT_TYPE,
)
from ralph.mcp.tools.md_artifact import handle_submit_md_artifact
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.plumbing.smoke_plumbing import (
    SmokeRunParams,
    _build_smoke_prompt,
    _run_smoke_agent,
    _subagent_smoke_evidence,
)
from tests._artifact_format_docs_mock_workspace import MockWorkspace

if TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch

pytestmark = pytest.mark.smoke


def _smoke_markdown() -> str:
    return """---
type: smoke_test_result
status: passed
output_file: tmp/interactive-claude-smoke/todo-list.js
---
## Summary

- [SUM-1] Smoke test passed

## Observed Working

- [OK-1] created todo-list.js

## Headless Guide Checks

- [HG-1] tool activity
"""


def _claude_config() -> AgentConfig:
    return AgentConfig(
        cmd="claude -p",
        output_flag="--output-format=stream-json",
        can_commit=False,
        json_parser="claude",
        transport=AgentTransport.CLAUDE,
    )


def _agy_config() -> AgentConfig:
    return AgentConfig(
        cmd="agy",
        can_commit=False,
        json_parser="generic",
        transport=AgentTransport.AGY,
    )


class _SmokeSession:
    session_id = "sess-smoke"
    run_id = "interactive-claude-smoke"
    drain = "development"
    broker_secret = None

    def check_capability(self, capability: str) -> object:
        del capability
        return "approved"


def _make_params(
    tmp_path: Path,
    agent_name: str,
    config: AgentConfig,
) -> SmokeRunParams:
    relative_dir = (
        tmp_path / "tmp" / "interactive-claude-smoke"
        if config.transport == AgentTransport.CLAUDE
        else tmp_path / "tmp" / "interactive-agy-smoke"
    )
    relative_dir.mkdir(parents=True, exist_ok=True)
    output_file = relative_dir / "todo-list.js"
    output_file.write_text("// smoke output", encoding="utf-8")
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("smoke prompt", encoding="utf-8")
    return SmokeRunParams(
        agent_name=agent_name,
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


def test_smoke_plumbing_claude_branch_stamps_canonical_receipt(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Claude branch: a submitted smoke_test_result artifact yields a receipt."""
    workspace = MockWorkspace(tmp_path)
    params = _make_params(tmp_path, "claude/haiku", _claude_config())
    run_id = "interactive-claude-smoke"

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        raw_sink = kwargs.get("raw_output_sink")
        if isinstance(raw_sink, deque):
            raw_sink.append('{"type":"session","session_id":"sess-smoke"}')
            raw_sink.append('{"type":"tool_use","tool":"submit_artifact"}')
            raw_sink.append('{"type":"tool_result","tool":"submit_artifact"}')
            raw_sink.append("Task declared complete: smoke done")
        rendered_sink = kwargs.get("rendered_output_sink")
        if isinstance(rendered_sink, deque):
            rendered_sink.append("tool_use: submit_artifact")
            rendered_sink.append("tool_result: submit_artifact")
        handle_submit_md_artifact(
            _SmokeSession(),
            workspace,
            {
                "artifact_type": SMOKE_TEST_RESULT_ARTIFACT_TYPE,
                "content": _smoke_markdown(),
            },
        )
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    result = _run_smoke_agent(params, run_id=run_id)

    assert result.artifact_submitted.holds is True
    assert result.explicit_completion_seen.holds is False
    assert artifact_receipt_present(tmp_path, run_id, SMOKE_TEST_RESULT_ARTIFACT_TYPE)
    assert is_artifact_submitted(tmp_path, run_id, SMOKE_TEST_RESULT_ARTIFACT_TYPE)


def test_smoke_plumbing_agy_branch_promotes_direct_write_to_canonical_receipt(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """AGY branch: a direct artifact file write is promoted to a canonical receipt."""
    params = _make_params(tmp_path, "agy/test-model", _agy_config())
    run_id = "interactive-agy-smoke-test-model"

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        artifact_type = SMOKE_TEST_RESULT_ARTIFACT_TYPE
        artifact_path = tmp_path / ".agent" / "tmp" / f"{artifact_type}.md"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(_smoke_markdown(), encoding="utf-8")
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    assert not artifact_receipt_present(tmp_path, run_id, SMOKE_TEST_RESULT_ARTIFACT_TYPE)

    result = _run_smoke_agent(params, run_id=run_id)

    assert result.artifact_submitted.holds is True
    assert result.explicit_completion_seen.holds is True
    assert is_artifact_submitted(tmp_path, run_id, SMOKE_TEST_RESULT_ARTIFACT_TYPE)
    assert artifact_receipt_present(tmp_path, run_id, SMOKE_TEST_RESULT_ARTIFACT_TYPE)


def test_smoke_artifact_submitted_false_when_no_artifact(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    params = _make_params(tmp_path, "claude/haiku", _claude_config())
    run_id = "interactive-claude-smoke"

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        raw_sink = kwargs.get("raw_output_sink")
        if isinstance(raw_sink, deque):
            raw_sink.append('{"type":"session","session_id":"sess-smoke"}')
            raw_sink.append('{"type":"tool_use","tool":"submit_artifact"}')
            raw_sink.append("Task declared complete: smoke done")
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    result = _run_smoke_agent(params, run_id=run_id)
    assert result.artifact_submitted.holds is False


def test_smoke_artifact_submitted_false_when_artifact_malformed(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    params = _make_params(tmp_path, "claude/haiku", _claude_config())
    run_id = "interactive-claude-smoke"
    artifact_path = tmp_path / ".agent" / "tmp" / f"{SMOKE_TEST_RESULT_ARTIFACT_TYPE}.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        artifact_path.write_text("not valid markdown", encoding="utf-8")
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    result = _run_smoke_agent(params, run_id=run_id)
    assert result.artifact_submitted.holds is False


def test_smoke_artifact_submitted_true_when_artifact_present_and_valid(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    workspace = MockWorkspace(tmp_path)
    params = _make_params(tmp_path, "claude/haiku", _claude_config())
    run_id = "interactive-claude-smoke"

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        handle_submit_md_artifact(
            _SmokeSession(),
            workspace,
            {
                "artifact_type": SMOKE_TEST_RESULT_ARTIFACT_TYPE,
                "content": _smoke_markdown(),
            },
        )
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    result = _run_smoke_agent(params, run_id=run_id)
    assert result.artifact_submitted.holds is True


def test_smoke_artifact_submitted_uses_canonical_helper_not_raw_file_presence(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    call_args: list[tuple] = []

    def _spy_is_artifact_submitted(
        workspace_root: Path,
        run_id: str,
        artifact_type: str,
        **kwargs: object,
    ) -> bool:
        call_args.append((workspace_root, run_id, artifact_type))
        return is_artifact_submitted(workspace_root, run_id, artifact_type)

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.is_artifact_submitted",
        _spy_is_artifact_submitted,
    )

    params = _make_params(tmp_path, "claude/haiku", _claude_config())
    run_id = "interactive-claude-smoke"

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        handle_submit_md_artifact(
            _SmokeSession(),
            MockWorkspace(tmp_path),
            {
                "artifact_type": SMOKE_TEST_RESULT_ARTIFACT_TYPE,
                "content": _smoke_markdown(),
            },
        )
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    _run_smoke_agent(params, run_id=run_id)

    assert len(call_args) >= 1
    first_workspace, first_run_id, first_type = call_args[0]
    assert first_workspace == tmp_path
    assert first_run_id == "interactive-claude-smoke"
    assert first_type == SMOKE_TEST_RESULT_ARTIFACT_TYPE


def test_smoke_tmp_fallback_promotion_consistent_with_errors(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    ".agent/tmp fallback promoted by canonical helper should not report submission error."
    params = _make_params(tmp_path, "claude/haiku", _claude_config())
    run_id = "interactive-claude-smoke"
    artifact_type = SMOKE_TEST_RESULT_ARTIFACT_TYPE
    tmp_artifact_path = tmp_path / ".agent" / "tmp" / f"{artifact_type}.md"

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        tmp_artifact_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_artifact_path.write_text(_smoke_markdown(), encoding="utf-8")
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    assert not artifact_receipt_present(tmp_path, run_id, artifact_type)

    result = _run_smoke_agent(params, run_id=run_id)

    assert result.artifact_submitted.holds is True
    assert is_artifact_submitted(tmp_path, run_id, artifact_type)
    assert artifact_receipt_present(tmp_path, run_id, artifact_type)
    assert "smoke_test_result artifact was not submitted" not in result.errors


def test_agy_tool_activity_must_not_come_from_artifact(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """A model-authored ``headless_guide_checks`` self-report must not be trusted.

    Regression test for the AGY smoke self-certification bug: the smoke run
    used to read the persisted artifact's ``headless_guide_checks`` field and
    return ``tool_activity_seen=True`` whenever the agent wrote
    ``"tool activity"`` into the artifact, even when the transcript contained
    no parser-classified tool events and no workspace file was written. Tool
    activity must now come from authoritative runtime evidence only:
    parser-classified tool events, or actual workspace file-write side
    effects.

    This test drives ``_run_smoke_agent`` with a transcript that contains NO
    ``[plain] tool:`` line and deletes the pre-existing
    ``todo-list.js`` (which the ``_make_params`` helper would otherwise
    create) so the agent's only "evidence" of tool activity is the
    self-reporting ``headless_guide_checks`` field in the persisted
    artifact. The smoke run must fail with
    ``"no tool activity was observed"`` so a self-certified artifact can
    never produce a green parity result.
    """
    params = _make_params(tmp_path, "agy/test-model", _agy_config())
    run_id = "interactive-agy-smoke-test-model"
    artifact_path = tmp_path / ".agent" / "tmp" / "smoke_test_result.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    # The ``_make_params`` helper pre-creates the output file so the
    # smoke harness's ``file_created`` check has a path to inspect. The
    # agent's authoritative tool-activity signal is the file write, so a
    # pre-created file would mask the regression. Delete the pre-created
    # file and assert the agent does NOT recreate it.
    params.output_file.unlink()
    assert not params.output_file.exists(), (
        f"Test setup invariant: {params.output_file} should NOT exist before the run"
    )

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        # Transcript contains plain text only — no ``[plain] tool:`` line
        # for the parser to classify as ``type='tool_use'``.
        raw_sink = kwargs.get("raw_output_sink")
        if isinstance(raw_sink, deque):
            raw_sink.append("I am just talking, not invoking a tool.")
            raw_sink.append("I would have written a file but I did not.")
        rendered_sink = kwargs.get("rendered_output_sink")
        if isinstance(rendered_sink, deque):
            rendered_sink.append("I am just talking, not invoking a tool.")
            rendered_sink.append("I would have written a file but I did not.")
        # Artifact self-reports tool activity. The harness must NOT trust this.
        markdown = (
            _smoke_markdown()
            .replace(
                "[SUM-1] Smoke test passed",
                "[SUM-1] self-certified",
            )
            .replace(
                "[HG-1] tool activity",
                "[HG-1] tool activity\n- [HG-2] parser events",
            )
        )
        artifact_path.write_text(markdown, encoding="utf-8")
        # CRUCIALLY: do NOT write the workspace output file. The harness
        # must NOT trust the self-reported tool activity in the artifact.
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    result = _run_smoke_agent(params, run_id=run_id)

    # The artifact was promoted to a receipt, so ``artifact_submitted`` is True.
    assert result.artifact_submitted.holds is True
    # BUT the self-reported ``tool activity`` in headless_guide_checks must
    # NOT be trusted. The transcript had no parser-classified tool events
    # AND the agent did not write the workspace output file.
    assert result.file_created is False, (
        "Test invariant: the agent should not have written the workspace file"
    )
    assert result.tool_activity_seen.holds is False, (
        "Tool activity must come from authoritative parser/transport events "
        "or a real workspace file-write side effect, not from the "
        "agent-authored artifact's headless_guide_checks"
    )
    assert "no tool activity was observed" in result.errors, (
        f"Expected 'no tool activity was observed' in errors, got: {result.errors}"
    )


def test_agy_smoke_regression_promotes_fallback_and_records_trusted_completion(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """AGY fallback artifacts earn host completion after a clean agent exit."""
    params = _make_params(tmp_path, "agy/test-model", _agy_config())
    run_id = "interactive-agy-smoke-test-model"
    artifact_path = tmp_path / ".agent" / "tmp" / "smoke_test_result.md"

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        raw_sink = kwargs.get("raw_output_sink")
        if isinstance(raw_sink, deque):
            raw_sink.extend(
                (
                    "I created the requested todo list.",
                    "[plain] tool: createTodoList",
                    "The smoke task is complete.",
                )
            )
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(_smoke_markdown(), encoding="utf-8")
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    result = _run_smoke_agent(params, run_id=run_id)

    assert result.artifact_submitted.holds is True
    assert result.explicit_completion_seen.holds is True
    assert "completion sentinel was not observed" not in result.errors


def test_agy_smoke_regression_missing_artifact_is_reported_without_submit_instruction(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """S-2: stripping artifact instructions must still surface the artifact error."""
    params = _make_params(tmp_path, "agy/test-model", _agy_config())
    prompt = _build_smoke_prompt(
        "tmp/interactive-agy-smoke/todo-list.js",
        submit_artifact_tool_name="ralph_submit_md_artifact",
        transport=AgentTransport.AGY,
    )
    stripped_prompt = prompt.split("- Call `ralph_submit_md_artifact`", maxsplit=1)[0]
    assert "ralph_submit_md_artifact" not in stripped_prompt
    params.prompt_file.write_text(stripped_prompt, encoding="utf-8")

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        raw_sink = kwargs.get("raw_output_sink")
        if isinstance(raw_sink, deque):
            raw_sink.extend(
                (
                    "I created the todo list implementation.",
                    "[plain] tool: createTodoList",
                    "File created at tmp/interactive-agy-smoke/todo-list.js.",
                )
            )
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    result = _run_smoke_agent(params, run_id="interactive-agy-smoke-test-model")

    assert "smoke_test_result artifact was not submitted" in result.errors


def test_claude_smoke_regression_missing_artifact_is_reported_without_submit_instruction(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """S-3: stripped Claude submission instructions still report the missing artifact."""
    params = _make_params(tmp_path, "claude/haiku", _claude_config())
    prompt = _build_smoke_prompt(
        "tmp/interactive-claude-smoke/todo-list.js",
        submit_artifact_tool_name="ralph_submit_md_artifact",
        transport=AgentTransport.CLAUDE,
    )
    stripped_prompt = prompt.split("- Call `ralph_submit_md_artifact`", maxsplit=1)[0]
    assert "ralph_submit_md_artifact" not in stripped_prompt
    params.prompt_file.write_text(stripped_prompt, encoding="utf-8")

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        raw_sink = kwargs.get("raw_output_sink")
        if isinstance(raw_sink, deque):
            raw_sink.extend(
                (
                    "I created the todo list implementation.",
                    "tool_use: createTodoList",
                    "File created at tmp/interactive-claude-smoke/todo-list.js.",
                )
            )
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    result = _run_smoke_agent(params, run_id="interactive-claude-smoke")

    assert "smoke_test_result artifact was not submitted" in result.errors


def test_agy_smoke_completion_rejects_transcript_marker_without_durable_evidence(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """A transcript completion marker alone must not be accepted as completion for AGY.

    Regression test for the AGY completion-spoofing bug: the prompt used to
    instruct AGY to print ``Task declared complete:`` and the detector used
    to accept any line containing the substring. The substring check is
    spoofable — an agent that prints the marker without writing the artifact
    would have been reported as completed. The prompt no longer tells AGY to
    print a marker, and the completion detector now requires the durable,
    run-scoped completion sentinel for every transport.

    This test drives ``_run_smoke_agent`` with a transcript that contains
    ``Task declared complete:`` but writes no artifact. The smoke run must
    fail with ``"smoke_test_result artifact was not submitted"`` so a
    transcript-only marker can never produce a green parity result.
    """
    params = _make_params(tmp_path, "agy/test-model", _agy_config())
    run_id = "interactive-agy-smoke-test-model"
    artifact_path = tmp_path / ".agent" / "tmp" / "smoke_test_result.md"

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        raw_sink = kwargs.get("raw_output_sink")
        if isinstance(raw_sink, deque):
            raw_sink.append("I will create the todo list implementation.")
            raw_sink.append("[plain] tool: createTodoList")
            raw_sink.append("File created at tmp/interactive-agy-smoke/todo-list.js.")
            # Transcript marker — MUST NOT be trusted on its own.
            raw_sink.append("Task declared complete:")
        rendered_sink = kwargs.get("rendered_output_sink")
        if isinstance(rendered_sink, deque):
            rendered_sink.append("I will create the todo list implementation.")
            rendered_sink.append("tool_use: createTodoList")
            rendered_sink.append("File created at tmp/interactive-agy-smoke/todo-list.js.")
        # CRUCIALLY: no artifact is written. The harness must report a failure.
        # Remove any pre-existing artifact so receipt promotion does not see one.
        if artifact_path.exists():
            artifact_path.unlink()
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    result = _run_smoke_agent(params, run_id=run_id)

    # The transcript marker MUST NOT satisfy the AGY completion check.
    # The marker is in the transcript (raw output line emitted by the fake),
    # but no durable completion sentinel was persisted.
    assert result.explicit_completion_seen.holds is False, (
        "AGY explicit completion must require the durable sentinel, not the "
        "transcript 'Task declared complete:' marker. The marker alone is a "
        "spoofable signal and was removed from the AGY prompt precisely so "
        "the harness stops trusting it."
    )
    assert "completion sentinel was not observed" in result.errors
    assert "smoke_test_result artifact was not submitted" in result.errors, (
        f"Expected 'smoke_test_result artifact was not submitted' in errors, got: {result.errors}"
    )


def test_subagent_smoke_evidence_replays_live_multi_subagent_capture() -> None:
    """S-4: replay agy_wire_subagent.jsonl and assert dispatch/result counts match.

    The measured live capture dispatches exactly two real subagents via
    ``define_subagent`` + ``invoke_subagent`` (ordinary tool calls, correctly
    excluded from the subagent count per D3) and monitors them via
    ``manage_subagents`` (also excluded). Prior to the S-3/S-4 fixes this
    fixture replayed as ``dispatch_count=3`` through the old
    ``_subagent_smoke_evidence`` (the pre-fix parser normalized
    ``define_subagent`` to ``subagent``, inflating the count).
    """
    fixture = Path(__file__).parent / "display" / "_fixtures" / "agy_wire_subagent.jsonl"
    lines = fixture.read_text(encoding="utf-8").splitlines()
    config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)

    evidence = _subagent_smoke_evidence(config, lines)

    assert evidence.dispatch_count == 2, (
        f"expected exactly the two real subagent dispatches (define_subagent / "
        f"manage_subagents excluded), got {evidence.dispatch_count}"
    )
    assert evidence.dispatch_seen is True
    assert evidence.result_seen is True
    assert evidence.post_result_activity_seen is True
