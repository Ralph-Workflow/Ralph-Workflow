"""Tests for ralph/phases/review.py — review phase handler."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from git import Repo

from ralph.git.operations import get_head_sha
from ralph.phases import PhaseContext
from ralph.phases.review import (
    REVIEW_BASELINE_MARKER,
    handle_review,
    handle_review_analysis,
)
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import PipelineEvent
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path


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


class TestHandleReview:
    def _make_context(self) -> MagicMock:
        return MagicMock()

    def test_prepare_prompt_effect_returns_prompt_prepared(self) -> None:
        effect = MagicMock(spec=PreparePromptEffect)
        effect.iteration = 1
        ctx = self._make_context()

        result = handle_review(effect, ctx)
        assert result == [PipelineEvent.PROMPT_PREPARED]

    def test_invoke_agent_effect_returns_agent_success(self) -> None:
        effect = MagicMock(spec=InvokeAgentEffect)
        ctx = self._make_context()
        ctx.workspace.exists.side_effect = lambda path: path == ".agent/artifacts/issues.json"
        ctx.workspace.read.return_value = (
            '{"type":"issues","content":{"status":"clean","summary":"ok","issues":[]}}'
        )

        result = handle_review(effect, ctx)
        assert result == [PipelineEvent.AGENT_SUCCESS]

    def test_invoke_agent_effect_without_issues_artifact_returns_failed(self) -> None:
        effect = MagicMock(spec=InvokeAgentEffect)
        ctx = self._make_context()
        ctx.workspace.exists.return_value = False

        result = handle_review(effect, ctx)
        assert result == [PipelineEvent.FAILED]

    def test_other_effect_returns_empty_list(self) -> None:
        effect = MagicMock(spec=Effect)
        ctx = self._make_context()

        result = handle_review(effect, ctx)
        assert result == []

    def test_review_skips_when_no_new_commits(self, tmp_git_repo: Path) -> None:
        ctx = _fs_context(tmp_git_repo)
        head = get_head_sha(tmp_git_repo)
        marker_path = tmp_git_repo / REVIEW_BASELINE_MARKER
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(head, encoding="utf-8")

        effect = InvokeAgentEffect(
            agent_name="reviewer",
            phase="review",
            prompt_file="review.txt",
        )
        assert handle_review(effect, ctx) == [PipelineEvent.REVIEW_CLEAN]

    def test_review_proceeds_when_new_commits_exist(self, tmp_git_repo: Path) -> None:
        ctx = _fs_context(tmp_git_repo)
        baseline = get_head_sha(tmp_git_repo)
        marker_path = tmp_git_repo / REVIEW_BASELINE_MARKER
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(baseline, encoding="utf-8")
        issues_path = tmp_git_repo / ".agent" / "artifacts" / "issues.json"
        issues_path.parent.mkdir(parents=True, exist_ok=True)
        issues_path.write_text(
            '{"type":"issues","content":{"status":"clean","summary":"ok","issues":[]}}',
            encoding="utf-8",
        )

        repo = Repo(tmp_git_repo)
        (tmp_git_repo / "changed.txt").write_text("x")
        repo.index.add(["changed.txt"])
        repo.index.commit("new work")
        new_head = get_head_sha(tmp_git_repo)
        assert new_head != baseline

        effect = InvokeAgentEffect(
            agent_name="reviewer",
            phase="review",
            prompt_file="review.txt",
        )
        assert handle_review(effect, ctx) == [PipelineEvent.AGENT_SUCCESS]

        updated_marker = marker_path.read_text(encoding="utf-8").strip()
        assert updated_marker == new_head

    def test_review_first_pass_has_no_baseline(self, tmp_git_repo: Path) -> None:
        ctx = _fs_context(tmp_git_repo)
        issues_path = tmp_git_repo / ".agent" / "artifacts" / "issues.json"
        issues_path.parent.mkdir(parents=True, exist_ok=True)
        issues_path.write_text(
            '{"type":"issues","content":{"status":"clean","summary":"ok","issues":[]}}',
            encoding="utf-8",
        )
        effect = InvokeAgentEffect(
            agent_name="reviewer",
            phase="review",
            prompt_file="review.txt",
        )
        assert handle_review(effect, ctx) == [PipelineEvent.AGENT_SUCCESS]
        marker_path = tmp_git_repo / REVIEW_BASELINE_MARKER
        assert marker_path.exists()


class TestHandleReviewAnalysis:
    def _make_context(self) -> MagicMock:
        return MagicMock()

    def _mock_invoke_effect(self) -> MagicMock:
        effect = MagicMock(spec=InvokeAgentEffect)
        return effect

    def test_proceed_decision_returns_analysis_success(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = '{"status": "completed"}'

        result = handle_review_analysis(effect, ctx)
        assert result == [PipelineEvent.ANALYSIS_SUCCESS]

    def test_complete_decision_returns_analysis_success(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = '{"status": "unknown"}'  # maps to COMPLETE

        result = handle_review_analysis(effect, ctx)
        assert result == [PipelineEvent.ANALYSIS_SUCCESS]

    def test_revise_decision_returns_analysis_loopback(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = '{"status": "revise"}'

        result = handle_review_analysis(effect, ctx)
        assert result == [PipelineEvent.ANALYSIS_LOOPBACK]

    def test_failure_decision_returns_failed_event(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = '{"status": "failed"}'

        result = handle_review_analysis(effect, ctx)
        assert result == [PipelineEvent.FAILED]

    def test_escalate_decision_returns_failed_event(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = '{"status": "escalate"}'

        result = handle_review_analysis(effect, ctx)
        assert result == [PipelineEvent.FAILED]

    def test_missing_artifact_fails_closed(self) -> None:
        effect = self._mock_invoke_effect()
        ctx = self._make_context()
        ctx.workspace.exists.return_value = False

        result = handle_review_analysis(effect, ctx)
        assert result == [PipelineEvent.FAILED]

    def test_non_invoke_effect_returns_empty_list(self) -> None:
        effect = MagicMock(spec=PreparePromptEffect)
        ctx = self._make_context()

        result = handle_review_analysis(effect, ctx)
        assert result == []
