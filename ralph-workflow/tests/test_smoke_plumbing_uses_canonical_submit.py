"""Smoke plumbing must produce canonical receipts for both Claude and AGY branches.

The smoke harness has two submission paths:

- Claude branch: the agent calls ``handle_submit_artifact``.
- AGY branch: the agent writes ``.agent/artifacts/smoke_test_result.json`` directly
  because AGY headless mode does not reliably call Ralph's MCP tools.

Both paths must end with a run-scoped canonical receipt so the completion gate
has a single source of truth.
"""

from __future__ import annotations

import json
from collections import deque
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
from ralph.mcp.tools.artifact import handle_submit_artifact
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.plumbing.smoke_plumbing import SmokeRunParams, _run_smoke_agent
from tests.test_artifact_format_docs_mock_workspace import MockWorkspace

if TYPE_CHECKING:
    from pathlib import Path

    from _pytest.monkeypatch import MonkeyPatch

pytestmark = pytest.mark.smoke


def _smoke_payload() -> dict[str, object]:
    return {
        "status": "passed",
        "output_file": "tmp/interactive-claude-smoke/todo-list.js",
        "observed_working": ["created todo-list.js"],
        "observed_breaks": [],
        "headless_guide_checks": ["tool activity"],
        "summary": "Smoke test passed",
    }


def _outer_envelope() -> dict[str, object]:
    return {
        "name": SMOKE_TEST_RESULT_ARTIFACT_TYPE,
        "type": SMOKE_TEST_RESULT_ARTIFACT_TYPE,
        "content": _smoke_payload(),
    }


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
        handle_submit_artifact(
            _SmokeSession(),
            workspace,
            {
                "artifact_type": SMOKE_TEST_RESULT_ARTIFACT_TYPE,
                "content": json.dumps(_smoke_payload()),
            },
        )
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    result = _run_smoke_agent(params, run_id=run_id)

    assert result.artifact_submitted is True
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
        artifact_path = tmp_path / ".agent" / "artifacts" / f"{artifact_type}.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(json.dumps(_outer_envelope()), encoding="utf-8")
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    assert not artifact_receipt_present(tmp_path, run_id, SMOKE_TEST_RESULT_ARTIFACT_TYPE)

    result = _run_smoke_agent(params, run_id=run_id)

    assert result.artifact_submitted is True
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
    assert result.artifact_submitted is False


def test_smoke_artifact_submitted_false_when_artifact_malformed(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    params = _make_params(tmp_path, "claude/haiku", _claude_config())
    run_id = "interactive-claude-smoke"
    artifact_path = tmp_path / ".agent" / "artifacts" / f"{SMOKE_TEST_RESULT_ARTIFACT_TYPE}.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        artifact_path.write_text("not valid json", encoding="utf-8")
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    result = _run_smoke_agent(params, run_id=run_id)
    assert result.artifact_submitted is False


def test_smoke_artifact_submitted_true_when_artifact_present_and_valid(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    workspace = MockWorkspace(tmp_path)
    params = _make_params(tmp_path, "claude/haiku", _claude_config())
    run_id = "interactive-claude-smoke"

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        handle_submit_artifact(
            _SmokeSession(),
            workspace,
            {
                "artifact_type": SMOKE_TEST_RESULT_ARTIFACT_TYPE,
                "content": json.dumps(_smoke_payload()),
            },
        )
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    result = _run_smoke_agent(params, run_id=run_id)
    assert result.artifact_submitted is True


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
        handle_submit_artifact(
            _SmokeSession(),
            MockWorkspace(tmp_path),
            {
                "artifact_type": SMOKE_TEST_RESULT_ARTIFACT_TYPE,
                "content": json.dumps(_smoke_payload()),
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
    tmp_artifact_path = tmp_path / ".agent" / "tmp" / f"{artifact_type}.json"

    def _fake_execute_agent_effect(*args: object, **kwargs: object) -> PipelineEvent:
        tmp_artifact_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_artifact_path.write_text(json.dumps(_outer_envelope()), encoding="utf-8")
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    assert not artifact_receipt_present(tmp_path, run_id, artifact_type)

    result = _run_smoke_agent(params, run_id=run_id)

    assert result.artifact_submitted is True
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
    artifact_path = tmp_path / ".agent" / "artifacts" / "smoke_test_result.json"
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
        payload = {
            "status": "passed",
            "output_file": "tmp/interactive-agy-smoke/todo-list.js",
            "observed_working": ["created todo-list.js"],
            "observed_breaks": [],
            "headless_guide_checks": ["tool activity", "parser events"],
            "summary": "self-certified",
        }
        envelope = {
            "name": SMOKE_TEST_RESULT_ARTIFACT_TYPE,
            "type": SMOKE_TEST_RESULT_ARTIFACT_TYPE,
            "content": payload,
        }
        artifact_path.write_text(json.dumps(envelope), encoding="utf-8")
        # CRUCIALLY: do NOT write the workspace output file. The harness
        # must NOT trust the self-reported tool activity in the artifact.
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    result = _run_smoke_agent(params, run_id=run_id)

    # The artifact was promoted to a receipt, so ``artifact_submitted`` is True.
    assert result.artifact_submitted is True
    # BUT the self-reported ``tool activity`` in headless_guide_checks must
    # NOT be trusted. The transcript had no parser-classified tool events
    # AND the agent did not write the workspace output file.
    assert result.file_created is False, (
        "Test invariant: the agent should not have written the workspace file"
    )
    assert result.tool_activity_seen is False, (
        "Tool activity must come from authoritative parser/transport events "
        "or a real workspace file-write side effect, not from the "
        "agent-authored artifact's headless_guide_checks"
    )
    assert "no tool activity was observed" in result.errors, (
        f"Expected 'no tool activity was observed' in errors, got: {result.errors}"
    )


def test_agy_smoke_completion_requires_receipt_not_transcript_marker(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """A transcript completion marker alone must not be accepted as completion for AGY.

    Regression test for the AGY completion-spoofing bug: the prompt used to
    instruct AGY to print ``Task declared complete:`` and the detector used
    to accept any line containing the substring. The substring check is
    spoofable — an agent that prints the marker without writing the artifact
    would have been reported as completed. The prompt no longer tells AGY to
    print a marker, and the completion detector for AGY now requires the
    canonical receipt as the authoritative signal.

    This test drives ``_run_smoke_agent`` with a transcript that contains
    ``Task declared complete:`` but writes no artifact. The smoke run must
    fail with ``"smoke_test_result artifact was not submitted"`` so a
    transcript-only marker can never produce a green parity result.
    """
    params = _make_params(tmp_path, "agy/test-model", _agy_config())
    run_id = "interactive-agy-smoke-test-model"
    artifact_path = tmp_path / ".agent" / "artifacts" / "smoke_test_result.json"

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
    # The marker is in the transcript (raw output line emitted by the fake);
    # the harness must report the marker NOT seen for completion purposes
    # because no canonical receipt was promoted.
    assert result.explicit_completion_seen is False, (
        "AGY explicit completion must require the canonical receipt, not the "
        "transcript 'Task declared complete:' marker. The marker alone is a "
        "spoofable signal and was removed from the AGY prompt precisely so "
        "the harness stops trusting it."
    )
    assert "smoke_test_result artifact was not submitted" in result.errors, (
        f"Expected 'smoke_test_result artifact was not submitted' in errors, got: {result.errors}"
    )
