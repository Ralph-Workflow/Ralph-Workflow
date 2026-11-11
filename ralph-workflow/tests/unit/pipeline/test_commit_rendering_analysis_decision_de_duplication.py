from __future__ import annotations

import io
from datetime import UTC, datetime

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.snapshot import PipelineSnapshot


class TestAnalysisDecisionDeDuplication:
    def test_emit_analysis_result_does_not_emit_to_console(self) -> None:
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=120, color_system=None)
        pd = ParallelDisplay(make_display_context(console=console, env={"CI": "1"}))

        pd.emit_analysis_result("development_analysis", "proceed", "looks good")

        text = buf.getvalue()
        assert text == ""
        log = pd.subscriber.decision_log
        assert any(entry[1].lower() == "proceed" for entry in log)

    def test_plain_renderer_suppresses_analysis_for_dev_review_phases(self) -> None:
        stream: io.StringIO = io.StringIO()
        console = Console(file=stream, force_terminal=False, color_system=None, width=200)
        renderer = ParallelDisplay(make_display_context(console=console, env={}))

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

        assert "[analysis]" not in output

    def test_plain_renderer_emits_analysis_for_other_phases(self) -> None:
        stream: io.StringIO = io.StringIO()
        console = Console(file=stream, force_terminal=False, color_system=None, width=200)
        renderer = ParallelDisplay(make_display_context(console=console, env={}))

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

        assert "[analysis]" in output
        assert "revise" in output
