"""Black-box tests for ``ParallelDisplay.emit_completion_summary_panel`` (wt-007).

Pins the new emit method that consolidates the end-of-run completion
panel onto ParallelDisplay (closing the last free-function console.print
bypass at ``ralph.display.completion_summary.emit_completion_summary``).

The test is black-box: it constructs a StringIO-backed rich Console,
attaches a DisplayContext, builds a real ``PipelineSnapshot``, and
asserts the visible output. No real I/O, no time.sleep, no subprocess.

Each test must complete in < 0.1 s. The whole file is expected to
finish in < 0.5 s.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from io import StringIO

from rich.console import Console

from ralph.display.completion_summary import CompletionSummaryOptions
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.snapshot import PipelineSnapshot


def _make_snapshot(
    *,
    phase: str = "complete",
    plan_summary: str | None = "Build the feature",
    plan_scope_items: tuple[str, ...] = ("item A",),
    decision_log: tuple[tuple[str, str, str, str], ...] = (
        ("development_analysis", "proceed", "all green", "2026-04-21T00:00:00+00:00"),
        ("review_analysis", "revise", "nit fix", "2026-04-21T00:01:00+00:00"),
    ),
    total_agent_calls: int = 4,
    is_terminal_success: bool = True,
    is_terminal_failure: bool = False,
) -> PipelineSnapshot:
    return PipelineSnapshot(
        phase=phase,
        previous_phase=None,
        review_issues_found=False,
        interrupted_by_user=False,
        last_error=None,
        pr_url=None,
        push_count=1,
        total_agent_calls=total_agent_calls,
        total_continuations=1,
        total_fallbacks=0,
        total_retries=0,
        workers=(),
        prompt_path="PROMPT.md",
        prompt_preview=(),
        run_id="r1",
        created_at=datetime(2026, 4, 21, tzinfo=UTC),
        plan_summary=plan_summary,
        plan_scope_items=plan_scope_items,
        plan_total_steps=2,
        plan_current_step=2,
        plan_risks=(),
        decision_log=decision_log,
        is_terminal_success=is_terminal_success,
        is_terminal_failure=is_terminal_failure,
    )


def _display(
    *,
    force_terminal: bool = True,
    width: int = 120,
) -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(
        file=buf,
        force_terminal=force_terminal,
        color_system=("truecolor" if force_terminal else None),
        width=width,
    )
    ctx = make_display_context(console=console, env={})
    return ParallelDisplay(ctx), buf


def test_emit_completion_summary_panel_emits_section_rule_header() -> None:
    """The chosen ``[run-completion]`` section-rule header is emitted in non-compact mode."""
    pd, buf = _display(force_terminal=True)
    pd.emit_completion_summary_panel(
        _make_snapshot(),
        options=CompletionSummaryOptions(),
    )
    pd.stop()
    output = buf.getvalue()
    assert "[run-completion]" in output, (
        f"expected [run-completion] section rule in output: {output!r}"
    )


def test_emit_completion_summary_panel_section_rule_at_any_width() -> None:
    """Single default-mode: section rule is emitted at any width (no compact-mode suppression)."""
    pd, buf = _display(force_terminal=False, width=40)
    pd.emit_completion_summary_panel(
        _make_snapshot(),
        options=CompletionSummaryOptions(),
    )
    pd.stop()
    output = buf.getvalue()
    # Section rule is emitted unconditionally in the single default-mode layout.
    assert "[run-completion]" in output, (
        f"default mode must emit the section rule; got: {output!r}"
    )
    # Body must still be present (Pipeline title and decisions survive).
    assert "Pipeline" in output, f"default-mode body must still be present: {output!r}"


def test_emit_completion_summary_panel_renders_panel_body() -> None:
    """The body preserves Pipeline Complete, Decisions, and agent_calls / METRICS content."""
    pd, buf = _display(force_terminal=True)
    pd.emit_completion_summary_panel(
        _make_snapshot(),
        options=CompletionSummaryOptions(),
    )
    pd.stop()
    output = buf.getvalue()
    assert "Pipeline Complete" in output, f"missing pipeline title: {output!r}"
    assert "Decisions" in output, f"missing decisions section: {output!r}"
    assert "agent_calls=4" in output or "METRICS" in output, (
        f"missing metrics / agent_calls body: {output!r}"
    )


def test_emit_completion_summary_panel_failed_uses_failed_title() -> None:
    """Failure snapshot uses 'Pipeline Failed' title in the rendered body."""
    pd, buf = _display(force_terminal=True)
    pd.emit_completion_summary_panel(
        _make_snapshot(
            phase="failed",
            is_terminal_success=False,
            is_terminal_failure=True,
        ),
        options=CompletionSummaryOptions(),
    )
    sys.stderr.write(f"\nDEBUG before stop output: {buf.getvalue()!r}\n")
    sys.stderr.flush()
    pd.stop()
    output = buf.getvalue()
    sys.stderr.write(f"\nDEBUG failure test output: {output!r}\n")
    sys.stderr.flush()
    assert "Pipeline Failed" in output, (
        f"expected 'Pipeline Failed' title in failure body: {output!r}"
    )


def test_emit_completion_summary_panel_quiet_mode_still_renders() -> None:
    """Quiet mode renders the completion panel (the only emit_* method that does).

    Unlike every other ``emit_*`` method, the completion summary panel
    intentionally does NOT short-circuit on ``is_quiet=True``: the user
    who runs the pipeline in ``--quiet`` mode still needs to see the
    final result. ``test_runner_quiet_mode.py`` and
    ``tests/integration/test_transcript_end_to_end.py`` pin this contract
    end-to-end; this test pins it at the unit level.
    """
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=120, color_system=None)
    ctx = make_display_context(console=console, env={})
    pd = ParallelDisplay(ctx, is_quiet=True)
    pd.emit_completion_summary_panel(
        _make_snapshot(),
        options=CompletionSummaryOptions(),
    )
    pd.stop()
    output = buf.getvalue()
    assert "Pipeline Complete" in output, (
        f"quiet mode must still render the completion panel; got: {output!r}"
    )
