import json
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from ralph.mcp.artifacts.commit_message import (
    COMMIT_MESSAGE_ARTIFACT,
    write_commit_message_artifact,
)
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


@lru_cache(maxsize=1)
def _default_policy():
    with tempfile.TemporaryDirectory() as tmp:
        return load_policy(Path(tmp) / ".agent")


def _mk_policy_context(workspace=None) -> PhaseContext:
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


def _stub_context(*, commit_message_present: bool = False) -> PhaseContext:
    workspace = MagicMock()
    workspace.exists.side_effect = lambda path: (
        commit_message_present and path == COMMIT_MESSAGE_ARTIFACT
    )
    workspace.read.side_effect = lambda path: (
        json.dumps(
            {
                "name": "commit_message",
                "type": "commit_message",
                "content": {"type": "commit", "subject": "fix(test): validate commit artifact"},
                "created_at": "STATIC",
                "updated_at": "STATIC",
                "metadata": {},
            }
        )
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


def _fs_context(root: Path, *, write_commit_message: bool = False) -> PhaseContext:
    workspace = FsWorkspace(root)
    if write_commit_message:
        commit_msg_path = root / COMMIT_MESSAGE_ARTIFACT
        commit_msg_path.parent.mkdir(parents=True, exist_ok=True)
        commit_msg_path.write_text(
            json.dumps(
                {
                    "name": "commit_message",
                    "type": "commit_message",
                    "content": {"type": "commit", "subject": "fix(test): validate commit artifact"},
                    "created_at": "STATIC",
                    "updated_at": "STATIC",
                    "metadata": {},
                }
            ),
            encoding="utf-8",
        )
    return PhaseContext.construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        agents_policy=object(),
        artifacts_policy=object(),
    )


def test_development_commit_defers_to_runner_on_invoke_agent(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "README.md").write_text("dirty")
    ctx = _fs_context(tmp_git_repo, write_commit_message=True)
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


def test_review_commit_defers_to_runner_on_invoke_agent(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "README.md").write_text("dirty")
    ctx = _fs_context(tmp_git_repo, write_commit_message=True)
    effect = InvokeAgentEffect(
        agent_name="review",
        phase="review_commit",
        prompt_file="review-plan.txt",
    )

    assert handle_commit_phase(effect, ctx) == []


def test_development_commit_emits_skip_when_no_diff(tmp_git_repo: Path) -> None:
    ctx = _fs_context(tmp_git_repo)
    effect = InvokeAgentEffect(
        agent_name="dev",
        phase="development_commit",
        prompt_file="dev-plan.txt",
    )

    assert handle_commit_phase(effect, ctx) == [PipelineEvent.COMMIT_SKIPPED]


def test_development_commit_defers_when_diff_exists(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "README.md").write_text("dirty")
    ctx = _fs_context(tmp_git_repo, write_commit_message=True)
    effect = InvokeAgentEffect(
        agent_name="dev",
        phase="development_commit",
        prompt_file="dev-plan.txt",
    )

    assert handle_commit_phase(effect, ctx) == []


def test_development_commit_missing_commit_message_emits_retry_in_session(
    tmp_git_repo: Path,
) -> None:
    (tmp_git_repo / "README.md").write_text("dirty")
    ctx = _fs_context(tmp_git_repo)  # no commit_message written
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
    tmp_git_repo: Path,
) -> None:
    (tmp_git_repo / "README.md").write_text("dirty")
    commit_msg_path = tmp_git_repo / COMMIT_MESSAGE_ARTIFACT
    commit_msg_path.parent.mkdir(parents=True, exist_ok=True)
    commit_msg_path.write_text("{not json", encoding="utf-8")
    ctx = _fs_context(tmp_git_repo)
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


def test_review_commit_emits_skip_when_no_diff(tmp_git_repo: Path) -> None:
    ctx = _fs_context(tmp_git_repo)
    effect = InvokeAgentEffect(
        agent_name="review",
        phase="review_commit",
        prompt_file="review-plan.txt",
    )

    assert handle_commit_phase(effect, ctx) == [PipelineEvent.COMMIT_SKIPPED]


def test_review_commit_defers_when_diff_exists(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "README.md").write_text("dirty")
    ctx = _fs_context(tmp_git_repo, write_commit_message=True)
    effect = InvokeAgentEffect(
        agent_name="review",
        phase="review_commit",
        prompt_file="review-plan.txt",
    )

    assert handle_commit_phase(effect, ctx) == []


def test_review_commit_missing_commit_message_emits_retry_in_session(
    tmp_git_repo: Path,
) -> None:
    (tmp_git_repo / "README.md").write_text("dirty")
    ctx = _fs_context(tmp_git_repo)  # no commit_message written
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
    tmp_git_repo: Path,
) -> None:
    """When the agent submits a skip artifact, handle_commit_phase must return COMMIT_SKIPPED.

    This prevents the runner from creating a git commit whose subject is literally
    'SKIP: reason' — the skip response must be honoured at the phase-handler layer.
    """
    (tmp_git_repo / "dirty.py").write_text("untracked_only = True\n")
    ctx = _fs_context(tmp_git_repo)  # worktree is dirty (untracked file)
    write_commit_message_artifact(tmp_git_repo, {"type": "skip", "reason": "no diff available"})
    effect = InvokeAgentEffect(
        agent_name="dev",
        phase="development_commit",
        prompt_file="dev-plan.txt",
    )

    result = handle_commit_phase(effect, ctx)
    assert result == [PipelineEvent.COMMIT_SKIPPED]


def test_review_commit_emits_skip_when_agent_submits_skip_artifact(
    tmp_git_repo: Path,
) -> None:
    """Same as above but for a review-role commit phase."""
    (tmp_git_repo / "dirty.py").write_text("untracked_only = True\n")
    ctx = _fs_context(tmp_git_repo)
    write_commit_message_artifact(tmp_git_repo, {"type": "skip", "reason": "no pending changes"})
    effect = InvokeAgentEffect(
        agent_name="review",
        phase="review_commit",
        prompt_file="review-plan.txt",
    )

    result = handle_commit_phase(effect, ctx)
    assert result == [PipelineEvent.COMMIT_SKIPPED]


def test_handle_commit_delegates_based_on_phase(tmp_git_repo: Path) -> None:
    (tmp_git_repo / "README.md").write_text("dirty")
    ctx = _fs_context(tmp_git_repo, write_commit_message=True)
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

    effect = PreparePromptEffect(phase="planning", iteration=3)

    assert handle_execution_phase(effect, ctx) == [PipelineEvent.PROMPT_PREPARED]
    assert not draft_path.exists()


def test_handle_planning_invokes_agent_successfully() -> None:
    ctx = _mk_policy_context()
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
    workspace.exists.return_value = True
    workspace.read.return_value = (
        '{"work_units":[{"unit_id":"u1","description":"A","allowed_directories":["src"],'
        '"dependencies":["missing"]}]}'
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
    workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.json"
    workspace.read.return_value = (
        '{"type":"plan","content":{"summary":{"context":"Plan MCP rollout","scope_items":['
        '{"text":"Update validation"},{"text":"Add tests"},{"text":"Update prompts"}]},'
        '"steps":[{"number":1,"title":"Validate plan","content":"Do the work"}],'
        '"critical_files":{"primary_files":[{"path":"ralph/mcp/tool_artifact.py","action":"modify"}]},'
        '"risks_mitigations":[{"risk":"Schema drift","mitigation":"HTTP tests"}],'
        '"verification_strategy":[{"method":"pytest","expected_outcome":"passes"}]}}'
    )

    effect = InvokeAgentEffect(agent_name="planner", phase="planning", prompt_file="planning.txt")

    assert handle_execution_phase(effect, ctx) == [PipelineEvent.AGENT_SUCCESS]
    workspace.read.assert_called_once_with(".agent/artifacts/plan.json")


def test_handle_planning_invalid_plan_schema_emits_retry_in_session() -> None:
    ctx = _mk_policy_context()
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
    workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.json"
    workspace.read.return_value = '{"type":"plan","content":{"noop":true}}'

    effect = InvokeAgentEffect(agent_name="planner", phase="planning", prompt_file="planning.txt")

    assert handle_execution_phase(effect, ctx) == [PipelineEvent.AGENT_SUCCESS]


def test_handle_development_reads_wrapped_plan_artifact_and_validates_schema() -> None:
    ctx = _mk_policy_context()
    workspace = cast("MagicMock", ctx.workspace)
    plan_json = (
        '{"type":"plan","content":{"summary":{"context":"Plan MCP rollout","scope_items":['
        '{"text":"Update validation"},{"text":"Add tests"},{"text":"Update prompts"}]},'
        '"steps":[{"number":1,"title":"Validate plan","content":"Do the work"}],'
        '"critical_files":{"primary_files":[{"path":"ralph/mcp/tool_artifact.py","action":"modify"}]},'
        '"risks_mitigations":[{"risk":"Schema drift","mitigation":"HTTP tests"}],'
        '"verification_strategy":[{"method":"pytest","expected_outcome":"passes"}]}}'
    )
    dev_result_json = (
        '{"type":"development_result","content":{"status":"completed",'
        '"summary":"Done.","files_changed":"- src/a.py",'
        '"plan_items_proven":['
        '{"plan_item":"Step 1: Validate plan",'
        '"proof":"Validated the wrapped plan artifact."}'
        "]}}"
    )
    workspace.exists.side_effect = lambda path: (
        path
        in {
            ".agent/artifacts/plan.json",
            ".agent/artifacts/development_result.json",
        }
    )
    workspace.read.side_effect = lambda path: (
        dev_result_json if path == ".agent/artifacts/development_result.json" else plan_json
    )

    effect = InvokeAgentEffect(
        agent_name="developer", phase="development", prompt_file="development.txt"
    )

    assert handle_execution_phase(effect, ctx) == [PipelineEvent.AGENT_SUCCESS]
    workspace.read.assert_any_call(".agent/artifacts/plan.json")


def test_handle_development_skips_when_plan_is_noop() -> None:
    ctx = _mk_policy_context()
    workspace = cast("MagicMock", ctx.workspace)
    workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.json"
    workspace.read.return_value = '{"type":"plan","content":{"noop":true}}'

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
    workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.json"
    workspace.read.return_value = '{"type":"plan","content":{"noop":true}}'

    effect = InvokeAgentEffect(
        agent_name="developer",
        phase="development_analysis",
        prompt_file="development_analysis.txt",
    )

    assert handle_generic_analysis_phase(effect, ctx) == [
        AnalysisDecisionEvent(phase="development_analysis", decision="completed")
    ]


def test_handle_development_analysis_skips_empty_steps_plan() -> None:
    """handle_generic_analysis_phase emits a completed decision for noop fallback plans."""
    ctx = _stub_context_no_exists()
    workspace = cast("MagicMock", ctx.workspace)
    workspace.exists.side_effect = lambda path: path == ".agent/artifacts/plan.json"
    # is_noop_plan fallback requires BOTH steps AND work_units to be empty lists
    workspace.read.return_value = '{"type":"plan","content":{"steps":[],"work_units":[]}}'

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

    result = handle_generic_analysis_phase(effect, ctx)
    assert len(result) == 1
    event = result[0]
    assert isinstance(event, PhaseFailureEvent)
    assert event.phase == "development_analysis"
    assert event.recoverable is True
    assert "development_analysis_decision" in event.reason
