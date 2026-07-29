"""Tests for ralph/phases/review.py — review phase handler."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.phases.review import handle_review
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import PhaseFailureEvent, PipelineEvent

if TYPE_CHECKING:
    import pytest


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
        ctx.workspace.exists.side_effect = lambda path: path == ".agent/artifacts/issues.md"
        ctx.workspace.read.return_value = """---
type: issues
status: no_issues
---

## Summary
- [SUM-1] Review passed.

## Issues

## What Came Up Short

## How To Fix
"""

        result = handle_review(effect, ctx)
        assert result == [PipelineEvent.AGENT_SUCCESS]

    def test_markdown_artifact_ignores_stale_legacy_json(self) -> None:
        effect = MagicMock(spec=InvokeAgentEffect)
        effect.phase = "review"
        ctx = self._make_context()
        artifacts = {
            ".agent/artifacts/issues.md": """---
type: issues
status: issues_found
---

## Summary
- [SUM-1] Canonical review found a defect.

## Issues
- [I-1] src/app.py | high | Canonical markdown issue

## What Came Up Short
- [W-1] The implementation omitted a required guard.

## How To Fix
- [FIX-1] Add the guard and cover the failure path.
""",
            ".agent/artifacts/issues.json": (
                '{"type":"issues","content":{"status":"clean","summary":"stale","issues":[]}}'
            ),
        }
        ctx.workspace.exists.side_effect = artifacts.__contains__
        ctx.workspace.read.side_effect = artifacts.__getitem__

        result = handle_review(effect, ctx)

        assert result == [PipelineEvent.REVIEW_ISSUES_FOUND]
        ctx.workspace.read.assert_any_call(".agent/artifacts/issues.md")
        assert ctx.workspace.read.call_count == 1

    def test_review_regression_stray_json_fails_closed(self) -> None:
        """Regression for PROMPT.md's Markdown-only tooling requirement.

        A stray legacy ``issues.json`` must never be parsed. The phase fails
        closed with an actionable error naming the canonical ``.md`` path and
        the Markdown submit tool.
        """
        effect = MagicMock(spec=InvokeAgentEffect)
        effect.phase = "review"
        ctx = self._make_context()
        ctx.workspace.exists.side_effect = lambda path: path == ".agent/artifacts/issues.json"
        ctx.workspace.read.side_effect = FileNotFoundError

        result = handle_review(effect, ctx)

        assert len(result) == 1
        event = result[0]
        assert isinstance(event, PhaseFailureEvent)
        assert event.recoverable is True
        assert "unsupported legacy JSON" in event.reason
        assert ".agent/artifacts/issues.md" in event.reason
        assert "ralph_submit_md_artifact" in event.reason
        ctx.workspace.read.assert_called_once_with(".agent/artifacts/issues.md")

    def test_invoke_agent_effect_without_issues_artifact_returns_phase_failure_recoverable(
        self,
    ) -> None:
        effect = MagicMock(spec=InvokeAgentEffect)
        effect.phase = "review"
        ctx = self._make_context()
        ctx.workspace.exists.return_value = False

        result = handle_review(effect, ctx)
        assert len(result) == 1
        event = result[0]
        assert isinstance(event, PhaseFailureEvent)
        assert event.phase == "review"
        assert event.recoverable is True
        assert "issues" in event.reason.lower() or "artifact" in event.reason.lower()

    def test_other_effect_returns_empty_list(self) -> None:
        effect = MagicMock(spec=Effect)
        ctx = self._make_context()

        result = handle_review(effect, ctx)
        assert result == []

    def test_review_skips_when_no_new_commits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unchanged baseline skips review without opening a Git repository."""
        ctx = self._make_context()
        monkeypatch.setattr("ralph.phases.review._read_review_baseline", lambda _ctx: "baseline")
        monkeypatch.setattr(
            "ralph.phases.review._has_new_commits_since_baseline", lambda _ctx, _baseline: False
        )

        effect = InvokeAgentEffect(
            agent_name="reviewer",
            phase="review",
            prompt_file="review.txt",
        )
        assert handle_review(effect, ctx) == [PipelineEvent.REVIEW_CLEAN]

    def test_review_proceeds_when_new_commits_exist(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A changed head processes the artifact and records the fresh baseline."""
        effect = MagicMock(spec=InvokeAgentEffect)
        effect.phase = "review"
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = """---
type: issues
status: no_issues
---

## Summary
- [SUM-1] Review passed.

## Issues

## What Came Up Short

## How To Fix
"""
        written: list[str] = []
        monkeypatch.setattr("ralph.phases.review._read_review_baseline", lambda _ctx: "baseline")
        monkeypatch.setattr(
            "ralph.phases.review._has_new_commits_since_baseline", lambda _ctx, _baseline: True
        )
        monkeypatch.setattr("ralph.phases.review._current_head_sha", lambda _ctx: "new-head")
        monkeypatch.setattr(
            "ralph.phases.review._write_review_baseline", lambda _ctx, sha: written.append(sha)
        )

        assert handle_review(effect, ctx) == [PipelineEvent.AGENT_SUCCESS]
        assert written == ["new-head"]

    def test_review_first_pass_has_no_baseline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A first review proceeds and writes its discovered head baseline."""
        effect = MagicMock(spec=InvokeAgentEffect)
        effect.phase = "review"
        ctx = self._make_context()
        ctx.workspace.exists.return_value = True
        ctx.workspace.read.return_value = """---
type: issues
status: no_issues
---

## Summary
- [SUM-1] Review passed.

## Issues

## What Came Up Short

## How To Fix
"""
        written: list[str] = []
        monkeypatch.setattr("ralph.phases.review._read_review_baseline", lambda _ctx: None)
        monkeypatch.setattr("ralph.phases.review._current_head_sha", lambda _ctx: "first-head")
        monkeypatch.setattr(
            "ralph.phases.review._write_review_baseline", lambda _ctx, sha: written.append(sha)
        )

        assert handle_review(effect, ctx) == [PipelineEvent.AGENT_SUCCESS]
        assert written == ["first-head"]
