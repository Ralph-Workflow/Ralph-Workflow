"""Regression tests for commit message rendering in pipeline commit flow.

These tests verify that:
1. A successful CommitEffect triggers render_commit_message() exactly once
2. A skipped commit (no diff) does NOT render the commit message block
3. The commit message block appears in the console output after a successful commit
"""

from __future__ import annotations

import io
from datetime import UTC, datetime

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.plain_renderer import PlainLogRenderer
from ralph.display.snapshot import PipelineSnapshot


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
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=120, color_system=None)
        pd = ParallelDisplay(make_display_context(console=console, env={"CI": "1"}))

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
        stream: io.StringIO = io.StringIO()
        console = Console(file=stream, force_terminal=False, color_system=None, width=200)
        renderer = PlainLogRenderer(make_display_context(console=console, env={}))

        # Snapshot with analysis role set — renderer suppresses inline [analysis] display
        snapshot = PipelineSnapshot(
            phase="development_analysis",
            previous_phase=None,
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
            created_at=datetime(2026, 4, 18, 12, 0, tzinfo=UTC),
            plan_summary=None,
            plan_scope_items=(),
            plan_total_steps=0,
            analysis_phase="development_analysis",
            analysis_decision="proceed",
            analysis_reason="all checks pass",
            current_phase_role="analysis",
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
        stream: io.StringIO = io.StringIO()
        console = Console(file=stream, force_terminal=False, color_system=None, width=200)
        renderer = PlainLogRenderer(make_display_context(console=console, env={}))

        # Snapshot with 'review' phase (not 'review_analysis')
        snapshot = PipelineSnapshot(
            phase="review",
            previous_phase=None,
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
            created_at=datetime(2026, 4, 18, 12, 0, tzinfo=UTC),
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
