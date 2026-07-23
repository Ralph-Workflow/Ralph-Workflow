import os
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from ralph.mcp.artifacts.commit_message import COMMIT_MESSAGE_ARTIFACT
from ralph.phases import (
    HANDLERS,
    PhaseContext,
    PhaseHandlerNotFoundError,
    handle_phase,
    register_handler,
)
from ralph.phases.analysis import handle_generic_analysis_phase
from ralph.phases.commit import handle_commit_phase
from ralph.phases.execution import handle_execution_phase
from ralph.pipeline.effects import (
    CommitEffect,
    Effect,
    InvokeAgentEffect,
    PreparePromptEffect,
)
from ralph.pipeline.events import AnalysisDecisionEvent, Event, PhaseFailureEvent, PipelineEvent
from ralph.policy.loader import load_policy
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.memory import MemoryWorkspace


@lru_cache(maxsize=1)
def _default_policy() -> object:
    with tempfile.TemporaryDirectory() as tmp:
        return load_policy(Path(tmp) / ".agent")


def _mk_policy_context(workspace: object = None) -> PhaseContext:
    """Context with real policy and mocked (nothing-exists) workspace."""
    policy = _default_policy()
    ws = workspace if workspace is not None else MagicMock()
    if workspace is None:
        ws.exists.return_value = False
    return PhaseContext.construct(
        workspace=ws,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=policy.pipeline,
        artifacts_policy=policy.artifacts,
        agents_policy=object(),
    )


_COMMIT_MESSAGE_DOC = """---
type: commit
subject: fix(test): validate commit artifact
---
"""

_PLAN_DOC = """---
type: plan
schema_version: 1
intent_verb: add
---
## Summary
Validate phase behavior with a markdown plan.

Intent: Exercise the phase artifact boundary.
Coverage: test

## Scope
- [SC-1] Validate planning output
  Category: test
- [SC-2] Hand the plan to development
  Category: test
- [SC-3] Preserve retry behavior
  Category: test

## Skills MCP
Skills: test-driven-development
MCPs: none

## Steps

### [S-1] Validate the markdown plan
Exercise the phase artifact boundary.

Type: file_change
Files:
- modify ralph/phases/execution.py
Satisfies: AC-01

## Critical Files
- [CF-1] ralph/phases/execution.py
  Action: modify
  Changes: consume the markdown plan

## Design
Use the canonical markdown artifact as the phase input.

Outcome: Phase handlers consume a validated plan document.

## Acceptance Criteria
- [AC-01] The phase accepts the markdown plan
  Satisfied by: S-1
  Verify: pytest tests/test_phases_commit_planning_fix.py -q

## Risks
- [R-1] Artifact path drift
  Severity: low
  Mitigation: Assert the canonical path read by the phase.

## Verification
- [V-1] pytest tests/test_phases_commit_planning_fix.py -q
  Expect: focused tests pass
"""

_DEVELOPMENT_RESULT_DOC = """---
type: development_result
status: completed
---
## Summary

- [SUM-1] Validated phase consumption of the markdown plan.

## Files Changed

- [F-1] ralph/phases/execution.py

## Plan Items Proven

- [S-1] The phase loaded and validated the canonical plan artifact.

## Analysis Items Addressed

- [FIX-1] No analysis items required changes.
"""

_NOOP_PLAN_DOC = """---
type: plan
noop: true
---
"""


def _stub_context(*, commit_message_present: bool = False) -> PhaseContext:
    workspace = MagicMock()
    workspace.exists.side_effect = lambda path: (
        commit_message_present and path == COMMIT_MESSAGE_ARTIFACT
    )
    workspace.read.side_effect = lambda path: (
        _COMMIT_MESSAGE_DOC
        if commit_message_present and path == COMMIT_MESSAGE_ARTIFACT
        else ""
    )
    return PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        agents_policy=object(),
        artifacts_policy=object(),
    )


def _stub_context_no_exists() -> PhaseContext:
    """Stub context where nothing exists."""
    workspace = MagicMock()
    workspace.exists.return_value = False
    return PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        agents_policy=object(),
        artifacts_policy=object(),
    )


def _commit_context(
    monkeypatch: pytest.MonkeyPatch,
    *,
    has_uncommitted_changes: bool,
    commit_message: str | None = None,
) -> PhaseContext:
    workspace = MemoryWorkspace()
    monkeypatch.setattr(
        "ralph.phases.commit.has_uncommitted_changes",
        lambda _root: has_uncommitted_changes,
    )
    if commit_message is not None:
        workspace.write(COMMIT_MESSAGE_ARTIFACT, commit_message)
    return PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        agents_policy=object(),
        artifacts_policy=object(),
    )


def test_development_commit_defers_to_runner_on_invoke_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _commit_context(
        monkeypatch,
        has_uncommitted_changes=True,
        commit_message=_COMMIT_MESSAGE_DOC,
    )
    effect = InvokeAgentEffect(
        agent_name="dev",
        phase="development_commit",
        prompt_file="dev-plan.txt",
    )

    assert handle_commit_phase(effect, ctx) == []


def test_development_commit_ignores_prepare_prompt_effect() -> None:
    ctx = _stub_context_no_exists()
    effect = PreparePromptEffect(
        phase="development_commit",
        iteration=1,
    )

    assert handle_commit_phase(effect, ctx) == []


def test_review_commit_defers_to_runner_on_invoke_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _commit_context(
        monkeypatch,
        has_uncommitted_changes=True,
        commit_message=_COMMIT_MESSAGE_DOC,
    )
    effect = InvokeAgentEffect(
        agent_name="review",
        phase="review_commit",
        prompt_file="review-plan.txt",
    )

    assert handle_commit_phase(effect, ctx) == []


def test_development_commit_emits_skip_when_no_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _commit_context(monkeypatch, has_uncommitted_changes=False)
    effect = InvokeAgentEffect(
        agent_name="dev",
        phase="development_commit",
        prompt_file="dev-plan.txt",
    )

    assert handle_commit_phase(effect, ctx) == [PipelineEvent.COMMIT_SKIPPED]


def test_development_commit_defers_when_diff_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _commit_context(
        monkeypatch,
        has_uncommitted_changes=True,
        commit_message=_COMMIT_MESSAGE_DOC,
    )
    effect = InvokeAgentEffect(
        agent_name="dev",
        phase="development_commit",
        prompt_file="dev-plan.txt",
    )

    assert handle_commit_phase(effect, ctx) == []


def test_development_commit_missing_commit_message_emits_retry_in_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _commit_context(monkeypatch, has_uncommitted_changes=True)
    effect = InvokeAgentEffect(
        agent_name="dev",
        phase="development_commit",
        prompt_file="dev-plan.txt",
    )

    result = handle_commit_phase(effect, ctx)
    assert len(result) == 1
    event = result[0]
    assert isinstance(event, PhaseFailureEvent)
    assert event.phase == "development_commit"
    assert event.recoverable is True
    assert event.retry_in_session is True


def test_development_commit_invalid_commit_message_emits_retry_in_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _commit_context(
        monkeypatch,
        has_uncommitted_changes=True,
        commit_message="---\ntype: commit\nsubject: not a conventional subject\n---\n",
    )
    effect = InvokeAgentEffect(
        agent_name="dev",
        phase="development_commit",
        prompt_file="dev-plan.txt",
    )

    result = handle_commit_phase(effect, ctx)
    assert len(result) == 1
    event = result[0]
    assert isinstance(event, PhaseFailureEvent)
    assert event.phase == "development_commit"
    assert event.recoverable is True
    assert event.retry_in_session is True
    assert "invalid" in event.reason.lower() or "empty" in event.reason.lower()


def test_review_commit_emits_skip_when_no_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _commit_context(monkeypatch, has_uncommitted_changes=False)
    effect = InvokeAgentEffect(
        agent_name="review",
        phase="review_commit",
        prompt_file="review-plan.txt",
    )

    assert handle_commit_phase(effect, ctx) == [PipelineEvent.COMMIT_SKIPPED]


def test_review_commit_defers_when_diff_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _commit_context(
        monkeypatch,
        has_uncommitted_changes=True,
        commit_message=_COMMIT_MESSAGE_DOC,
    )
    effect = InvokeAgentEffect(
        agent_name="review",
        phase="review_commit",
        prompt_file="review-plan.txt",
    )

    assert handle_commit_phase(effect, ctx) == []


def test_review_commit_missing_commit_message_emits_retry_in_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _commit_context(monkeypatch, has_uncommitted_changes=True)
    effect = InvokeAgentEffect(
        agent_name="review",
        phase="review_commit",
        prompt_file="review-plan.txt",
    )

    result = handle_commit_phase(effect, ctx)
    assert len(result) == 1
    event = result[0]
    assert isinstance(event, PhaseFailureEvent)
    assert event.phase == "review_commit"
    assert event.recoverable is True
    assert event.retry_in_session is True


def test_development_commit_emits_skip_when_agent_submits_skip_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the agent submits a skip artifact, handle_commit_phase must return COMMIT_SKIPPED.

    This prevents the runner from creating a git commit whose subject is literally
    'SKIP: reason' — the skip response must be honoured at the phase-handler layer.
    """
    ctx = _commit_context(
        monkeypatch,
        has_uncommitted_changes=True,
        commit_message="---\ntype: skip\nreason: no diff available\n---\n",
    )
    effect = InvokeAgentEffect(
        agent_name="dev",
        phase="development_commit",
        prompt_file="dev-plan.txt",
    )

    result = handle_commit_phase(effect, ctx)
    assert result == [PipelineEvent.COMMIT_SKIPPED]


def test_review_commit_emits_skip_when_agent_submits_skip_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same as above but for a review-role commit phase."""
    ctx = _commit_context(
        monkeypatch,
        has_uncommitted_changes=True,
        commit_message="---\ntype: skip\nreason: no pending changes\n---\n",
    )
    effect = InvokeAgentEffect(
        agent_name="review",
        phase="review_commit",
        prompt_file="review-plan.txt",
    )

    result = handle_commit_phase(effect, ctx)
    assert result == [PipelineEvent.COMMIT_SKIPPED]


def test_handle_commit_delegates_based_on_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _commit_context(
        monkeypatch,
        has_uncommitted_changes=True,
        commit_message=_COMMIT_MESSAGE_DOC,
    )
    effect = InvokeAgentEffect(
        agent_name="dev",
        phase="development_commit",
        prompt_file="plan.md",
    )

    assert handle_commit_phase(effect, ctx) == []


def test_handle_commit_returns_empty_for_prepare_prompt_and_review_phase() -> None:
    ctx = _stub_context_no_exists()
    effect = PreparePromptEffect(
        phase="review_commit",
        iteration=2,
    )

    assert handle_commit_phase(effect, ctx) == []


def test_handle_commit_returns_empty_for_unknown_phase() -> None:
    ctx = _stub_context_no_exists()
    effect = PreparePromptEffect(phase="custom", iteration=0)

    assert handle_commit_phase(effect, ctx) == []


def test_handle_planning_prepares_prompt_and_advances() -> None:
    ctx = _mk_policy_context()
    effect = PreparePromptEffect(phase="planning", iteration=3)

    assert handle_execution_phase(effect, ctx) == [PipelineEvent.PROMPT_PREPARED]


def test_handle_planning_prepare_prompt_preserves_resumable_plan_draft(
    tmp_path: Path,
) -> None:
    workspace = FsWorkspace(tmp_path)
    policy = _default_policy()
    ctx = PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=policy.pipeline,
        agents_policy=object(),
        artifacts_policy=policy.artifacts,
    )
    draft_path = tmp_path / ".agent" / "artifacts" / ".plan.draft.md"
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(_PLAN_DOC.replace("plan", "resumable plan", 1), encoding="utf-8")

    effect = PreparePromptEffect(phase="planning", iteration=3)

    assert handle_execution_phase(effect, ctx) == [PipelineEvent.PROMPT_PREPARED]
    assert draft_path.exists()


def test_handle_planning_prepare_prompt_clears_draft_when_final_plan_is_newer(
    tmp_path: Path,
) -> None:
    workspace = FsWorkspace(tmp_path)
    policy = _default_policy()
    ctx = PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=policy.pipeline,
        agents_policy=object(),
        artifacts_policy=policy.artifacts,
    )
    artifact_dir = tmp_path / ".agent" / "artifacts"
    draft_path = artifact_dir / ".plan.draft.md"
    plan_path = artifact_dir / "plan.md"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(_PLAN_DOC.replace("plan", "stale plan", 1), encoding="utf-8")
    plan_path.write_text(_PLAN_DOC, encoding="utf-8")
    os.utime(draft_path, (1, 1))
    os.utime(plan_path, (2, 2))

    effect = PreparePromptEffect(phase="planning", iteration=3)

    assert handle_execution_phase(effect, ctx) == [PipelineEvent.PROMPT_PREPARED]
    assert not draft_path.exists()


def test_handle_planning_invokes_agent_successfully() -> None:
    ctx = _mk_policy_context()
    workspace = cast("MagicMock", ctx.workspace)
    workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.md"
    workspace.read.return_value = _PLAN_DOC
    effect = InvokeAgentEffect(
        agent_name="planner",
        phase="planning",
        prompt_file="planning.txt",
    )

    assert handle_execution_phase(effect, ctx) == [PipelineEvent.AGENT_SUCCESS]


def test_handle_planning_missing_plan_artifact_emits_retry_in_session() -> None:
    ctx = _mk_policy_context()
    workspace = cast("MagicMock", ctx.workspace)
    workspace.exists.return_value = False

    effect = InvokeAgentEffect(
        agent_name="planner",
        phase="planning",
        prompt_file="planning.txt",
    )

    result = handle_execution_phase(effect, ctx)
    assert len(result) == 1
    event = result[0]
    assert isinstance(event, PhaseFailureEvent)
    assert event.phase == "planning"
    assert event.recoverable is True
    assert event.retry_in_session is True


def test_handle_planning_invalid_work_units_emits_retry_in_session() -> None:
    ctx = _mk_policy_context()
    workspace = cast("MagicMock", ctx.workspace)
    workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.md"
    workspace.read.return_value = (
        _PLAN_DOC
        + """

## Work Units
- [u1] Implement the change
  Directories: src
  Depends on: missing
"""
    )

    effect = InvokeAgentEffect(
        agent_name="planner",
        phase="planning",
        prompt_file="planning.txt",
    )

    result = handle_execution_phase(effect, ctx)
    assert len(result) == 1
    event = result[0]
    assert isinstance(event, PhaseFailureEvent)
    assert event.phase == "planning"
    assert event.recoverable is True
    assert event.retry_in_session is True


def test_handle_planning_reads_plan_artifact_path_and_validates_schema() -> None:
    ctx = _mk_policy_context()
    workspace = cast("MagicMock", ctx.workspace)
    workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.md"
    workspace.read.return_value = _PLAN_DOC

    effect = InvokeAgentEffect(agent_name="planner", phase="planning", prompt_file="planning.txt")

    assert handle_execution_phase(effect, ctx) == [PipelineEvent.AGENT_SUCCESS]
    workspace.read.assert_called_once_with(".agent/artifacts/plan.md")


def test_handle_planning_invalid_plan_schema_emits_retry_in_session() -> None:
    ctx = _mk_policy_context()
    workspace = cast("MagicMock", ctx.workspace)
    workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.md"
    workspace.read.return_value = _PLAN_DOC.replace("## Skills MCP", "## Unsupported")

    effect = InvokeAgentEffect(agent_name="planner", phase="planning", prompt_file="planning.txt")

    result = handle_execution_phase(effect, ctx)
    assert len(result) == 1
    event = result[0]
    assert isinstance(event, PhaseFailureEvent)
    assert event.phase == "planning"
    assert event.recoverable is True
    assert event.retry_in_session is True


def test_handle_planning_accepts_noop_plan() -> None:
    ctx = _mk_policy_context()
    workspace = cast("MagicMock", ctx.workspace)
    workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.md"
    workspace.read.return_value = _NOOP_PLAN_DOC

    effect = InvokeAgentEffect(agent_name="planner", phase="planning", prompt_file="planning.txt")

    assert handle_execution_phase(effect, ctx) == [PipelineEvent.AGENT_SUCCESS]


def test_handle_development_reads_wrapped_plan_artifact_and_validates_schema() -> None:
    ctx = _mk_policy_context()
    workspace = cast("MagicMock", ctx.workspace)
    plan_doc = _PLAN_DOC
    workspace.exists.side_effect = lambda path: (
        path
        in {
            ".agent/artifacts/plan.md",
            ".agent/artifacts/development_result.md",
        }
    )
    workspace.read.side_effect = lambda path: (
        _DEVELOPMENT_RESULT_DOC
        if path == ".agent/artifacts/development_result.md"
        else plan_doc
    )

    effect = InvokeAgentEffect(
        agent_name="developer", phase="development", prompt_file="development.txt"
    )

    assert handle_execution_phase(effect, ctx) == [PipelineEvent.AGENT_SUCCESS]
    workspace.read.assert_any_call(".agent/artifacts/plan.md")


def test_handle_development_skips_when_plan_is_noop() -> None:
    ctx = _mk_policy_context()
    workspace = cast("MagicMock", ctx.workspace)
    workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.md"
    workspace.read.return_value = _NOOP_PLAN_DOC

    effect = InvokeAgentEffect(
        agent_name="developer", phase="development", prompt_file="development.txt"
    )

    assert handle_execution_phase(effect, ctx) == [PipelineEvent.AGENT_SUCCESS]


def test_handle_planning_ignores_unrelated_effects() -> None:
    ctx = _mk_policy_context()
    effect = CommitEffect(message_file="message.txt")

    assert handle_execution_phase(effect, ctx) == []


def test_handle_phase_dispatches_to_registered_handler() -> None:
    ctx = _stub_context_no_exists()

    @register_handler("custom_phase")
    def _custom_handler(effect: Effect, context: PhaseContext) -> list[Event]:
        assert isinstance(effect, PreparePromptEffect | InvokeAgentEffect)
        assert effect.phase == "custom_phase"
        assert context is ctx
        return [PipelineEvent.COMPLETE]

    try:
        handler_effect = PreparePromptEffect(phase="custom_phase", iteration=1)
        assert handle_phase(handler_effect, ctx) == [PipelineEvent.COMPLETE]
    finally:
        HANDLERS.pop("custom_phase", None)


def test_handle_phase_raises_when_handler_missing() -> None:
    ctx = _stub_context_no_exists()
    effect = CommitEffect(message_file="missing.txt")

    with pytest.raises(PhaseHandlerNotFoundError) as excinfo:
        handle_phase(effect, ctx)

    assert "unknown" in str(excinfo.value)


def test_handle_development_analysis_skips_when_plan_is_noop() -> None:
    """handle_generic_analysis_phase emits a completed decision when plan is a no-op."""
    ctx = _stub_context_no_exists()
    workspace = cast("MagicMock", ctx.workspace)
    workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.md"
    workspace.read.return_value = _NOOP_PLAN_DOC

    effect = InvokeAgentEffect(
        agent_name="developer",
        phase="development_analysis",
        prompt_file="development_analysis.txt",
    )

    assert handle_generic_analysis_phase(effect, ctx) == [
        AnalysisDecisionEvent(phase="development_analysis", decision="completed")
    ]


def test_handle_development_analysis_skips_minimal_noop_plan() -> None:
    """The explicit minimal no-op document short-circuits analysis."""
    ctx = _stub_context_no_exists()
    workspace = cast("MagicMock", ctx.workspace)
    workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.md"
    workspace.read.return_value = _NOOP_PLAN_DOC

    effect = InvokeAgentEffect(
        agent_name="developer",
        phase="development_analysis",
        prompt_file="development_analysis.txt",
    )

    assert handle_generic_analysis_phase(effect, ctx) == [
        AnalysisDecisionEvent(phase="development_analysis", decision="completed")
    ]


def test_handle_dev_analysis_non_noop_missing_decision_is_recoverable() -> None:
    """Missing analysis evidence should retry instead of terminally failing.

    When a real development run finishes without submitting
    development_analysis_decision.json, Ralph should treat that as an incomplete
    agent attempt and route it through normal retry/fallback handling.
    """
    ctx = _stub_context_no_exists()
    workspace = cast("MagicMock", ctx.workspace)
    # plan.md exists but is not a no-op.
    workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.md"
    workspace.read.return_value = _PLAN_DOC

    effect = InvokeAgentEffect(
        agent_name="developer",
        phase="development_analysis",
        prompt_file="development_analysis.txt",
    )

    result = handle_generic_analysis_phase(effect, ctx)
    assert len(result) == 1
    event = result[0]
    assert isinstance(event, PhaseFailureEvent)
    assert event.phase == "development_analysis"
    assert event.recoverable is True
    assert "development_analysis_decision" in event.reason
