import json
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from ralph.config.enums import (
    PHASE_DEVELOPMENT_COMMIT,
    PHASE_FIX,
    PHASE_PLANNING,
    PHASE_REVIEW_COMMIT,
)
from ralph.phases import (
    HANDLERS,
    PhaseContext,
    PhaseHandlerNotFoundError,
    handle_phase,
    register_handler,
)
from ralph.phases.commit import (
    handle_commit,
    handle_development_commit,
    handle_review_commit,
)
from ralph.phases.development import handle_development, handle_development_analysis
from ralph.phases.fix import handle_fix
from ralph.phases.planning import handle_planning
from ralph.pipeline.effects import (
    CommitEffect,
    Effect,
    InvokeAgentEffect,
    PreparePromptEffect,
)
from ralph.pipeline.events import Event, PhaseFailureEvent, PipelineEvent
from ralph.workspace.fs import FsWorkspace


def _stub_context() -> PhaseContext:
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


def _fs_context(root: Path) -> PhaseContext:
    workspace = FsWorkspace(root)
    return PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        agents_policy=object(),
        artifacts_policy=object(),
    )


def test_development_commit_defers_to_runner_on_invoke_agent() -> None:
    ctx = _stub_context()
    effect = InvokeAgentEffect(
        agent_name="dev",
        phase=PHASE_DEVELOPMENT_COMMIT,
        prompt_file="dev-plan.txt",
    )

    assert handle_development_commit(effect, ctx) == []


def test_development_commit_ignores_prepare_prompt_effect() -> None:
    ctx = _stub_context()
    effect = PreparePromptEffect(
        phase=PHASE_DEVELOPMENT_COMMIT,
        iteration=1,
    )

    assert handle_development_commit(effect, ctx) == []


def test_review_commit_defers_to_runner_on_invoke_agent() -> None:
    ctx = _stub_context()
    effect = InvokeAgentEffect(
        agent_name="review",
        phase=PHASE_REVIEW_COMMIT,
        prompt_file="review-plan.txt",
    )

    assert handle_review_commit(effect, ctx) == []


def test_development_commit_emits_skip_when_no_diff(tmp_git_repo: Path) -> None:
    ctx = _fs_context(tmp_git_repo)
    effect = InvokeAgentEffect(
        agent_name="dev",
        phase=PHASE_DEVELOPMENT_COMMIT,
        prompt_file="dev-plan.txt",
    )

    assert handle_development_commit(effect, ctx) == [PipelineEvent.COMMIT_SKIPPED]


def test_development_commit_defers_when_diff_exists(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "README.md").write_text("dirty")
    ctx = _fs_context(tmp_git_repo)
    effect = InvokeAgentEffect(
        agent_name="dev",
        phase=PHASE_DEVELOPMENT_COMMIT,
        prompt_file="dev-plan.txt",
    )

    assert handle_development_commit(effect, ctx) == []


def test_review_commit_emits_skip_when_no_diff(tmp_git_repo: Path) -> None:
    ctx = _fs_context(tmp_git_repo)
    effect = InvokeAgentEffect(
        agent_name="review",
        phase=PHASE_REVIEW_COMMIT,
        prompt_file="review-plan.txt",
    )

    assert handle_review_commit(effect, ctx) == [PipelineEvent.COMMIT_SKIPPED]


def test_review_commit_defers_when_diff_exists(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "README.md").write_text("dirty")
    ctx = _fs_context(tmp_git_repo)
    effect = InvokeAgentEffect(
        agent_name="review",
        phase=PHASE_REVIEW_COMMIT,
        prompt_file="review-plan.txt",
    )

    assert handle_review_commit(effect, ctx) == []


def test_handle_commit_delegates_based_on_phase() -> None:
    ctx = _stub_context()
    effect = InvokeAgentEffect(
        agent_name="dev",
        phase=PHASE_DEVELOPMENT_COMMIT,
        prompt_file="plan.md",
    )

    assert handle_commit(effect, ctx) == []


def test_handle_commit_returns_empty_for_prepare_prompt_and_review_phase() -> None:
    ctx = _stub_context()
    effect = PreparePromptEffect(
        phase=PHASE_REVIEW_COMMIT,
        iteration=2,
    )

    assert handle_commit(effect, ctx) == []


def test_handle_commit_returns_empty_for_unknown_phase() -> None:
    ctx = _stub_context()
    effect = PreparePromptEffect(phase="custom", iteration=0)

    assert handle_commit(effect, ctx) == []


def test_handle_planning_prepares_prompt_and_advances() -> None:
    ctx = _stub_context()
    effect = PreparePromptEffect(phase=PHASE_PLANNING, iteration=3)

    assert handle_planning(effect, ctx) == [PipelineEvent.PROMPT_PREPARED]


def test_handle_planning_prepare_prompt_preserves_resumable_plan_draft(
    tmp_path: Path,
) -> None:
    workspace = FsWorkspace(tmp_path)
    ctx = PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        agents_policy=object(),
        artifacts_policy=object(),
    )
    draft_path = tmp_path / ".agent" / "artifacts" / ".plan_draft.json"
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "started_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "sections": {
                    "summary": {
                        "context": "Resume planning",
                        "scope_items": [
                            {"text": "one"},
                            {"text": "two"},
                            {"text": "three"},
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    effect = PreparePromptEffect(phase=PHASE_PLANNING, iteration=3)

    assert handle_planning(effect, ctx) == [PipelineEvent.PROMPT_PREPARED]
    assert draft_path.exists()


def test_handle_planning_prepare_prompt_clears_draft_when_final_plan_is_newer(
    tmp_path: Path,
) -> None:
    workspace = FsWorkspace(tmp_path)
    ctx = PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        agents_policy=object(),
        artifacts_policy=object(),
    )
    artifact_dir = tmp_path / ".agent" / "artifacts"
    draft_path = artifact_dir / ".plan_draft.json"
    plan_path = artifact_dir / "plan.json"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "started_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "sections": {
                    "summary": {
                        "context": "Old planning run",
                        "scope_items": [
                            {"text": "one"},
                            {"text": "two"},
                            {"text": "three"},
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    plan_path.write_text(
        json.dumps(
            {
                "type": "plan",
                "content": {
                    "summary": {
                        "context": "Completed planning run",
                        "scope_items": [
                            {"text": "one"},
                            {"text": "two"},
                            {"text": "three"},
                        ],
                    },
                    "steps": [{"number": 1, "title": "x", "content": "y"}],
                    "critical_files": {
                        "primary_files": [
                            {"path": "ralph/mcp/tool_artifact.py", "action": "modify"}
                        ]
                    },
                    "risks_mitigations": [{"risk": "drift", "mitigation": "cleanup"}],
                    "verification_strategy": [{"method": "pytest", "expected_outcome": "passes"}],
                },
            }
        ),
        encoding="utf-8",
    )

    effect = PreparePromptEffect(phase=PHASE_PLANNING, iteration=3)

    assert handle_planning(effect, ctx) == [PipelineEvent.PROMPT_PREPARED]
    assert not draft_path.exists()


def test_handle_planning_invokes_agent_successfully() -> None:
    ctx = _stub_context()
    workspace = cast("MagicMock", ctx.workspace)
    workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.json"
    workspace.read.return_value = (
        '{"type":"plan","content":{"summary":{"context":"Plan handoff","scope_items":['
        '{"text":"Retry invalid planning output"},{"text":"Hand off to development"},'
        '{"text":"Verify policy-driven routing"}]},'
        '"steps":[{"number":1,"title":"Implement","content":"Wire the pipeline"}],'
        '"critical_files":{"primary_files":[{"path":"ralph/pipeline/runner.py","action":"modify"}]},'
        '"risks_mitigations":[{"risk":"Regression","mitigation":"Add tests"}],'
        '"verification_strategy":[{"method":"pytest","expected_outcome":"passes"}]}}'
    )
    effect = InvokeAgentEffect(
        agent_name="planner",
        phase=PHASE_PLANNING,
        prompt_file="planning.txt",
    )

    assert handle_planning(effect, ctx) == [PipelineEvent.AGENT_SUCCESS]


def test_handle_planning_missing_plan_artifact_returns_agent_failure() -> None:
    ctx = _stub_context()
    workspace = cast("MagicMock", ctx.workspace)
    workspace.exists.return_value = False

    effect = InvokeAgentEffect(
        agent_name="planner",
        phase=PHASE_PLANNING,
        prompt_file="planning.txt",
    )

    assert handle_planning(effect, ctx) == [PipelineEvent.AGENT_FAILURE]


def test_handle_planning_invalid_work_units_returns_agent_failure() -> None:
    ctx = _stub_context()
    workspace = cast("MagicMock", ctx.workspace)
    workspace.exists.return_value = True
    workspace.read.return_value = (
        '{"work_units":[{"unit_id":"u1","description":"A","allowed_directories":["src"],'
        '"dependencies":["missing"]}]}'
    )

    effect = InvokeAgentEffect(
        agent_name="planner",
        phase=PHASE_PLANNING,
        prompt_file="planning.txt",
    )

    assert handle_planning(effect, ctx) == [PipelineEvent.AGENT_FAILURE]


def test_handle_planning_reads_plan_artifact_path_and_validates_schema() -> None:
    ctx = _stub_context()
    workspace = cast("MagicMock", ctx.workspace)
    workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.json"
    workspace.read.return_value = (
        '{"type":"plan","content":{"summary":{"context":"Plan MCP rollout","scope_items":['
        '{"text":"Update validation"},{"text":"Add tests"},{"text":"Update prompts"}]},'
        '"steps":[{"number":1,"title":"Validate plan","content":"Do the work"}],'
        '"critical_files":{"primary_files":[{"path":"ralph/mcp/tool_artifact.py","action":"modify"}]},'
        '"risks_mitigations":[{"risk":"Schema drift","mitigation":"HTTP tests"}],'
        '"verification_strategy":[{"method":"pytest","expected_outcome":"passes"}]}}'
    )

    effect = InvokeAgentEffect(
        agent_name="planner", phase=PHASE_PLANNING, prompt_file="planning.txt"
    )

    assert handle_planning(effect, ctx) == [PipelineEvent.AGENT_SUCCESS]
    workspace.read.assert_called_once_with(".agent/artifacts/plan.json")


def test_handle_planning_invalid_plan_schema_returns_agent_failure() -> None:
    ctx = _stub_context()
    workspace = cast("MagicMock", ctx.workspace)
    workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.json"
    workspace.read.return_value = (
        '{"type":"plan","content":'
        '{"summary":{"context":"Plan MCP rollout","scope_items":[{"text":"Only one"}]},'
        '"steps":[{"number":1,"title":"Validate plan","content":"Do the work"}],'
        '"critical_files":{"primary_files":[{"path":"ralph/mcp/tool_artifact.py","action":"modify"}]},'
        '"risks_mitigations":[{"risk":"Schema drift","mitigation":"HTTP tests"}],'
        '"verification_strategy":[{"method":"pytest","expected_outcome":"passes"}]}}'
    )

    effect = InvokeAgentEffect(
        agent_name="planner", phase=PHASE_PLANNING, prompt_file="planning.txt"
    )

    assert handle_planning(effect, ctx) == [PipelineEvent.AGENT_FAILURE]


def test_handle_planning_accepts_noop_plan() -> None:
    ctx = _stub_context()
    workspace = cast("MagicMock", ctx.workspace)
    workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.json"
    workspace.read.return_value = '{"type":"plan","content":{"noop":true}}'

    effect = InvokeAgentEffect(
        agent_name="planner", phase=PHASE_PLANNING, prompt_file="planning.txt"
    )

    assert handle_planning(effect, ctx) == [PipelineEvent.AGENT_SUCCESS]


def test_handle_development_reads_wrapped_plan_artifact_and_validates_schema() -> None:
    ctx = _stub_context()
    workspace = cast("MagicMock", ctx.workspace)
    workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.json"
    workspace.read.return_value = (
        '{"type":"plan","content":{"summary":{"context":"Plan MCP rollout","scope_items":['
        '{"text":"Update validation"},{"text":"Add tests"},{"text":"Update prompts"}]},'
        '"steps":[{"number":1,"title":"Validate plan","content":"Do the work"}],'
        '"critical_files":{"primary_files":[{"path":"ralph/mcp/tool_artifact.py","action":"modify"}]},'
        '"risks_mitigations":[{"risk":"Schema drift","mitigation":"HTTP tests"}],'
        '"verification_strategy":[{"method":"pytest","expected_outcome":"passes"}]}}'
    )

    effect = InvokeAgentEffect(
        agent_name="developer", phase="development", prompt_file="development.txt"
    )

    assert handle_development(effect, ctx) == [PipelineEvent.AGENT_SUCCESS]
    workspace.read.assert_called_once_with(".agent/artifacts/plan.json")


def test_handle_development_skips_when_plan_is_noop() -> None:
    ctx = _stub_context()
    workspace = cast("MagicMock", ctx.workspace)
    workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.json"
    workspace.read.return_value = '{"type":"plan","content":{"noop":true}}'

    effect = InvokeAgentEffect(
        agent_name="developer", phase="development", prompt_file="development.txt"
    )

    assert handle_development(effect, ctx) == [PipelineEvent.AGENT_SUCCESS]


def test_handle_planning_ignores_unrelated_effects() -> None:
    ctx = _stub_context()
    effect = CommitEffect(message_file="message.txt")

    assert handle_planning(effect, ctx) == []


def test_handle_fix_prepares_prompt_with_iteration_context() -> None:
    ctx = _stub_context()
    effect = PreparePromptEffect(phase=PHASE_FIX, iteration=5)

    assert handle_fix(effect, ctx) == [PipelineEvent.PROMPT_PREPARED]


def test_handle_fix_invokes_agent_successfully() -> None:
    ctx = _stub_context()
    effect = InvokeAgentEffect(
        agent_name="fixer",
        phase=PHASE_FIX,
        prompt_file="fix.txt",
    )

    assert handle_fix(effect, ctx) == [PipelineEvent.AGENT_SUCCESS]


def test_handle_fix_succeeds_without_fix_result_artifact() -> None:
    ctx = _stub_context()
    effect = InvokeAgentEffect(
        agent_name="fixer",
        phase=PHASE_FIX,
        prompt_file="fix.txt",
    )

    assert handle_fix(effect, ctx) == [PipelineEvent.AGENT_SUCCESS]


def test_handle_fix_ignores_unrelated_effects() -> None:
    ctx = _stub_context()
    effect = CommitEffect(message_file="irrelevant.txt")

    assert handle_fix(effect, ctx) == []


def test_handle_phase_dispatches_to_registered_handler() -> None:
    ctx = _stub_context()

    @register_handler("custom_phase")
    def _custom_handler(effect: Effect, context: PhaseContext) -> list[Event]:
        assert isinstance(effect, (PreparePromptEffect, InvokeAgentEffect))
        assert effect.phase == "custom_phase"
        assert context is ctx
        return [PipelineEvent.COMPLETE]

    try:
        handler_effect = PreparePromptEffect(phase="custom_phase", iteration=1)
        assert handle_phase(handler_effect, ctx) == [PipelineEvent.COMPLETE]
    finally:
        HANDLERS.pop("custom_phase", None)


def test_handle_phase_raises_when_handler_missing() -> None:
    ctx = _stub_context()
    effect = CommitEffect(message_file="missing.txt")

    with pytest.raises(PhaseHandlerNotFoundError) as excinfo:
        handle_phase(effect, ctx)

    assert "unknown" in str(excinfo.value)


def test_handle_development_analysis_skips_when_plan_is_noop() -> None:
    """handle_development_analysis must short-circuit with ANALYSIS_SUCCESS when plan is a no-op."""
    ctx = _stub_context()
    workspace = cast("MagicMock", ctx.workspace)
    workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.json"
    workspace.read.return_value = '{"type":"plan","content":{"noop":true}}'

    effect = InvokeAgentEffect(
        agent_name="developer",
        phase="development_analysis",
        prompt_file="development_analysis.txt",
    )

    assert handle_development_analysis(effect, ctx) == [PipelineEvent.ANALYSIS_SUCCESS]


def test_handle_development_analysis_skips_empty_steps_plan() -> None:
    """handle_development_analysis must short-circuit when plan has empty steps
    AND empty work_units."""
    ctx = _stub_context()
    workspace = cast("MagicMock", ctx.workspace)
    workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.json"
    # is_noop_plan fallback requires BOTH steps AND work_units to be empty lists
    workspace.read.return_value = '{"type":"plan","content":{"steps":[],"work_units":[]}}'

    effect = InvokeAgentEffect(
        agent_name="developer",
        phase="development_analysis",
        prompt_file="development_analysis.txt",
    )

    assert handle_development_analysis(effect, ctx) == [PipelineEvent.ANALYSIS_SUCCESS]


def test_handle_dev_analysis_non_noop_returns_phase_failure_not_recoverable() -> None:
    """handle_development_analysis must parse analysis decision when plan is not a no-op.

    When plan is not a no-op, it falls through to parse_analysis_decision.
    Since there's no development_analysis_decision artifact, it returns FAILURE as a
    non-recoverable PhaseFailureEvent.
    """
    ctx = _stub_context()
    workspace = cast("MagicMock", ctx.workspace)
    # plan.json exists but is NOT a no-op
    workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.json"
    workspace.read.return_value = (
        '{"type":"plan","content":{"summary":{"context":"Real work","scope_items":'
        '[{"text":"Implement feature"}]},"steps":[{"number":1,"title":"Do it","content":"x"}]}}'
    )

    effect = InvokeAgentEffect(
        agent_name="developer",
        phase="development_analysis",
        prompt_file="development_analysis.txt",
    )

    result = handle_development_analysis(effect, ctx)
    assert len(result) == 1
    event = result[0]
    assert isinstance(event, PhaseFailureEvent)
    assert event.phase == "development_analysis"
    assert event.recoverable is False
