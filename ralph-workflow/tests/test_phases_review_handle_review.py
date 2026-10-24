"""Tests for ralph/phases/review.py — review phase handler.

Every test drives the PUBLIC ``handle_review`` entry point. The phase's
side-effecting collaborators (baseline reader, commit checker, head-SHA
reader, baseline writer) are supplied through the injectable
``ReviewCollaborators`` seam, so the suite never opens a Git repository and
never reaches into the module's private helpers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.phases.required_artifacts import retry_hint_path
from ralph.phases.review import ReviewCollaborators, handle_review
from ralph.pipeline.effects import Effect, InvokeAgentEffect, PreparePromptEffect
from ralph.pipeline.events import PhaseFailureEvent, PipelineEvent

if TYPE_CHECKING:
    from ralph.phases import PhaseContext


class _FakeReviewCollaborators:
    """In-memory stand-in for the review phase's injectable collaborators.

    The fakes are pure in-memory recorders: no filesystem access, no Git
    repository, and no sleeping. They record the calls the handler makes so
    the tests can assert on observable behavior instead of implementation
    internals.
    """

    def __init__(
        self,
        *,
        baseline: str | None = None,
        has_new_commits: bool = True,
        head: str | None = None,
    ) -> None:
        self.baseline = baseline
        self.has_new_commits = has_new_commits
        self.head = head
        self.written: list[str] = []
        self.commit_checks: list[str] = []

    def build(self) -> ReviewCollaborators:
        """Return the production-facing collaborator bundle."""
        return ReviewCollaborators(
            read_baseline=self._read_baseline,
            has_new_commits=self._has_new_commits_since_baseline,
            current_head_sha=self._current_head_sha,
            write_baseline=self._write_baseline,
        )

    def _read_baseline(self, _ctx: PhaseContext) -> str | None:
        return self.baseline

    def _has_new_commits_since_baseline(self, _ctx: PhaseContext, baseline: str) -> bool:
        self.commit_checks.append(baseline)
        return self.has_new_commits

    def _current_head_sha(self, _ctx: PhaseContext) -> str | None:
        return self.head

    def _write_baseline(self, _ctx: PhaseContext, sha: str) -> None:
        self.written.append(sha)


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
        collaborators = _FakeReviewCollaborators().build()

        result = handle_review(effect, ctx, collaborators=collaborators)
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
        collaborators = _FakeReviewCollaborators().build()

        result = handle_review(effect, ctx, collaborators=collaborators)

        assert result == [PipelineEvent.REVIEW_ISSUES_FOUND]
        ctx.workspace.read.assert_any_call(".agent/artifacts/issues.md")
        # The legacy ``.json`` path must never be opened when the canonical
        # ``.md`` is present; the production code only calls ``read`` on the
        # markdown path in this scenario. The exact call count is an
        # implementation detail and is intentionally not asserted.

    def test_accepted_issues_artifact_clears_retry_hint(self) -> None:
        effect = InvokeAgentEffect(agent_name="reviewer", phase="review", prompt_file="review.txt")
        ctx = self._make_context()
        hint_path = retry_hint_path("review")
        ctx.workspace.exists.side_effect = lambda path: (
            path
            in {
                ".agent/artifacts/issues.md",
                hint_path,
            }
        )
        ctx.workspace.read.return_value = """---
type: issues
status: issues_found
---

## Summary
- [SUM-1] Review found a defect.

## Issues
- [I-1] src/app.py | high | Add the missing guard.

## What Came Up Short
- [W-1] The implementation omitted a required guard.

## How To Fix
- [FIX-1] Add the guard and cover the failure path.
"""
        collaborators = _FakeReviewCollaborators().build()

        assert handle_review(effect, ctx, collaborators=collaborators) == [
            PipelineEvent.REVIEW_ISSUES_FOUND
        ]
        ctx.workspace.remove.assert_called_once_with(hint_path)

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
        collaborators = _FakeReviewCollaborators().build()

        result = handle_review(effect, ctx, collaborators=collaborators)

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
        collaborators = _FakeReviewCollaborators().build()

        result = handle_review(effect, ctx, collaborators=collaborators)
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

    def test_review_skips_when_no_new_commits(self) -> None:
        """An unchanged baseline skips review without opening a Git repository."""
        ctx = self._make_context()
        fakes = _FakeReviewCollaborators(baseline="baseline", has_new_commits=False)
        collaborators = fakes.build()

        effect = InvokeAgentEffect(
            agent_name="reviewer",
            phase="review",
            prompt_file="review.txt",
        )
        assert handle_review(effect, ctx, collaborators=collaborators) == [
            PipelineEvent.REVIEW_CLEAN
        ]
        assert fakes.commit_checks == ["baseline"]
        assert fakes.written == []

    def test_review_proceeds_when_new_commits_exist(self) -> None:
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
        fakes = _FakeReviewCollaborators(
            baseline="baseline",
            has_new_commits=True,
            head="new-head",
        )
        collaborators = fakes.build()

        assert handle_review(effect, ctx, collaborators=collaborators) == [
            PipelineEvent.AGENT_SUCCESS
        ]
        assert fakes.written == ["new-head"]
        assert fakes.commit_checks == ["baseline"]

    def test_review_first_pass_has_no_baseline(self) -> None:
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
        fakes = _FakeReviewCollaborators(baseline=None, has_new_commits=True, head="first-head")
        collaborators = fakes.build()

        assert handle_review(effect, ctx, collaborators=collaborators) == [
            PipelineEvent.AGENT_SUCCESS
        ]
        assert fakes.written == ["first-head"]
        # A missing baseline short-circuits the commit check entirely.
        assert fakes.commit_checks == []
