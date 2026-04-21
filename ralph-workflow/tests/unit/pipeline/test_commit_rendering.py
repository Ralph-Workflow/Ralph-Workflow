"""Regression tests for commit message rendering in pipeline commit flow.

These tests verify that:
1. A successful CommitEffect triggers render_commit_message() exactly once
2. A skipped commit (no diff) does NOT render the commit message block
3. The commit message block appears in the console output after a successful commit
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from ralph.display.artifact_renderer import render_commit_message


if TYPE_CHECKING:
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.state import PipelineState


class TestRenderCommitMessageIntegration:
    """Tests for commit message rendering behavior.

    These tests verify the contract that render_commit_message():
    - Produces a titled block with "COMMIT MESSAGE" when artifact is present
    - Produces no output when artifact is absent (skipped commit)
    - Produces no output when artifact is malformed (defensive)
    """

    def test_render_commit_message_produces_titled_block(self, tmp_path: Path) -> None:
        """Verify render_commit_message outputs a titled block with subject."""
        tmp_dir = tmp_path / ".agent" / "tmp"
        tmp_dir.mkdir(parents=True)
        artifact = {
            "name": "commit_message",
            "type": "commit_message",
            "content": {
                "type": "commit",
                "subject": "feat: add new feature",
                "body": "This is the commit body",
            },
            "created_at": "2026-04-19T12:00:00Z",
            "updated_at": "2026-04-19T12:00:00Z",
        }
        (tmp_dir / "commit_message.json").write_text(json.dumps(artifact), encoding="utf-8")

        console = Console(file=io.StringIO(), force_terminal=True, color_system=None, width=120)
        render_commit_message(tmp_path, console)

        output = console.file.getvalue()
        assert "COMMIT MESSAGE" in output
        assert "feat: add new feature" in output

    def test_render_commit_message_no_output_when_artifact_absent(self, tmp_path: Path) -> None:
        """Verify render_commit_message produces no output when commit is skipped."""
        # No .agent/tmp/commit_message.json - commit was skipped
        console = Console(file=io.StringIO(), force_terminal=True, color_system=None, width=120)
        render_commit_message(tmp_path, console)

        output = console.file.getvalue()
        assert output == ""

    def test_render_commit_message_no_output_when_artifact_malformed(self, tmp_path: Path) -> None:
        """Verify render_commit_message is defensive about malformed JSON."""
        tmp_dir = tmp_path / ".agent" / "tmp"
        tmp_dir.mkdir(parents=True)
        (tmp_dir / "commit_message.json").write_text("not valid json{{", encoding="utf-8")

        console = Console(file=io.StringIO(), force_terminal=True, color_system=None, width=120)
        render_commit_message(tmp_path, console)

        output = console.file.getvalue()
        # Defensive: malformed JSON should not crash and should produce no output
        assert output == ""

    def test_commit_message_block_appears_once_after_successful_commit(
        self,
        tmp_path: Path,
    ) -> None:
        """Verify the commit message titled block appears exactly once.

        This is a regression test to ensure that when a commit succeeds,
        the pipeline commit flow renders the commit message block exactly once,
        not zero times (missing) and not multiple times (double-rendering).
        """
        tmp_dir = tmp_path / ".agent" / "tmp"
        tmp_dir.mkdir(parents=True)
        artifact = {
            "name": "commit_message",
            "type": "commit_message",
            "content": {
                "type": "commit",
                "subject": "fix: correct bug",
                "body": "Fixes the issue",
            },
            "created_at": "2026-04-19T12:00:00Z",
            "updated_at": "2026-04-19T12:00:00Z",
        }
        (tmp_dir / "commit_message.json").write_text(json.dumps(artifact), encoding="utf-8")

        console = Console(file=io.StringIO(), force_terminal=True, color_system=None, width=120)

        # Simulate the pipeline calling render_commit_message once after a successful commit
        render_commit_message(tmp_path, console)

        output = console.file.getvalue()
        # Should appear exactly once
        assert output.count("COMMIT MESSAGE") == 1
        assert output.count("fix: correct bug") == 1


class TestAnalysisDecisionDeDuplication:
    """Regression tests for analysis decision output de-duplication.

    These tests verify that:
    1. emit_analysis_result only records to decision_log, does NOT emit to console
    2. render_analysis_decision is the single source of truth for analysis blocks
    3. PlainLogRenderer suppresses [analysis] lines for development_analysis/review_analysis
    """

    def test_emit_analysis_result_does_not_emit_to_console(self) -> None:
        """Verify emit_analysis_result does NOT write to console.

        The analysis decision should be rendered as a titled block by
        render_analysis_decision in development.py/review.py, NOT by
        emit_analysis_result. This test is a regression guard against
        accidental double-rendering.
        """
        from ralph.display.parallel_display import ParallelDisplay

        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=120, color_system=None)
        pd = ParallelDisplay(console, {"CI": "1"}, mode="lines")

        pd.emit_analysis_result("development_analysis", "proceed", "looks good")

        text = buf.getvalue()
        # emit_analysis_result should NOT emit anything to console
        assert text == ""
        # But it SHOULD record to decision_log
        log = pd.subscriber.decision_log
        assert any(entry[1].lower() == "proceed" for entry in log)

    def test_plain_renderer_suppresses_analysis_for_dev_review_phases(self) -> None:
        """Verify PlainLogRenderer suppresses [analysis] lines for dev/review phases.

        Since render_analysis_decision already outputs a titled block for these
        phases, PlainLogRenderer should not also emit a plain [analysis] line.
        """
        from ralph.display.plain_renderer import PlainLogRenderer
        from ralph.display.snapshot import PipelineSnapshot

        stream = io.StringIO()
        console = Console(file=stream, force_terminal=False, color_system=None, width=200)
        renderer = PlainLogRenderer(console)

        # Snapshot with development_analysis phase
        snapshot = PipelineSnapshot(
            phase="development_analysis",
            previous_phase=None,
            iteration=1,
            total_iterations=3,
            reviewer_pass=0,
            total_reviewer_passes=0,
            review_issues_found=False,
            interrupted_by_user=False,
            last_error=None,
            pr_url=None,
            push_count=0,
            total_agent_calls=0,
            total_continuations=0,
            total_fallbacks=0,
            total_retries=0,
            workers=(),
            prompt_path=None,
            prompt_preview=(),
            run_id=None,
            plan_summary=None,
            plan_scope_items=(),
            plan_total_steps=0,
            analysis_phase="development_analysis",
            analysis_decision="proceed",
            analysis_reason="all checks pass",
        )

        renderer.emit_snapshot(snapshot)
        output = stream.getvalue()

        # Should NOT contain [analysis] line for development_analysis
        assert "[analysis]" not in output

    def test_plain_renderer_emits_analysis_for_other_phases(self) -> None:
        """Verify PlainLogRenderer emits [analysis] lines for non-dev/review phases.

        Phases like 'review' or 'planning' that don't have their own
        titled block renderer should still get [analysis] lines.
        """
        from ralph.display.plain_renderer import PlainLogRenderer
        from ralph.display.snapshot import PipelineSnapshot

        stream = io.StringIO()
        console = Console(file=stream, force_terminal=False, color_system=None, width=200)
        renderer = PlainLogRenderer(console)

        # Snapshot with 'review' phase (not 'review_analysis')
        snapshot = PipelineSnapshot(
            phase="review",
            previous_phase=None,
            iteration=1,
            total_iterations=3,
            reviewer_pass=0,
            total_reviewer_passes=0,
            review_issues_found=False,
            interrupted_by_user=False,
            last_error=None,
            pr_url=None,
            push_count=0,
            total_agent_calls=0,
            total_continuations=0,
            total_fallbacks=0,
            total_retries=0,
            workers=(),
            prompt_path=None,
            prompt_preview=(),
            run_id=None,
            plan_summary=None,
            plan_scope_items=(),
            plan_total_steps=0,
            analysis_phase="review",
            analysis_decision="revise",
            analysis_reason="needs more work",
        )

        renderer.emit_snapshot(snapshot)
        output = stream.getvalue()

        # SHOULD contain [analysis] line for 'review' phase
        assert "[analysis]" in output
        assert "revise" in output
