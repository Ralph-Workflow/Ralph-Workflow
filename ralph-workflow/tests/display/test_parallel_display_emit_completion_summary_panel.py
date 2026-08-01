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

import dataclasses
import sys
from datetime import UTC, datetime
from io import StringIO
from typing import TYPE_CHECKING

from rich.console import Console

from ralph.display.completion_summary import CompletionSummaryOptions
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.snapshot import PipelineSnapshot

if TYPE_CHECKING:
    from pathlib import Path


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
    """The chosen ``[run-completion]`` section-rule header is emitted above the panel."""
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
    assert "[run-completion]" in output, f"default mode must emit the section rule; got: {output!r}"
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


# --- Regression tests for the wt-028-display review feedback ---


def test_emit_completion_panel_does_not_duplicate_commit_subject(tmp_path: Path) -> None:
    """The commit subject is rendered ONCE in the completion panel, not duplicated.

    The prior bug rendered the commit message lines in BOTH
    ``_commit_section`` AND ``_tail_items``, producing two copies of the
    subject line. The consolidated single default-mode layout renders
    the commit output in ``_commit_section`` only.
    """
    artifacts = tmp_path / ".agent" / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "commit_message.md").write_text(
        "---\ntype: commit\n"
        "subject: feat(display): surface polished completion output\n---\n\n"
        "## Body Summary\n\n"
        "- [S-1] Show the final commit message in the completion summary.\n",
        encoding="utf-8",
    )

    pd, buf = _display(force_terminal=True)
    pd.emit_completion_summary_panel(
        _make_snapshot(),
        options=CompletionSummaryOptions(workspace_root=tmp_path),
    )
    pd.stop()
    output = buf.getvalue()
    assert output.count("feat(display): surface polished completion output") == 1, (
        f"commit subject must appear exactly once in completion panel; got "
        f"{output.count('feat(display): surface polished completion output')} copies: {output!r}"
    )


def test_emit_completion_panel_pr_url_without_commit_artifact(tmp_path: Path) -> None:
    """PR URL is rendered even when no commit-message artifact exists.

    The prior bug returned early in ``_commit_section`` when
    ``commit_lines`` was empty, dropping the ``pr_url`` line entirely.
    The consolidated layout renders the PR URL independently of whether
    a commit artifact is present.
    """
    pd, buf = _display(force_terminal=True)
    snap = _make_snapshot()  # _make_snapshot sets pr_url=None; override
    snap_with_pr = dataclasses.replace(snap, pr_url="https://example.com/pr/42")
    pd.emit_completion_summary_panel(
        snap_with_pr,
        options=CompletionSummaryOptions(workspace_root=tmp_path),
    )
    pd.stop()
    output = buf.getvalue()
    assert "PR:" in output, (
        f"PR URL must render even when no commit artifact exists; got: {output!r}"
    )
    assert "https://example.com/pr/42" in output, (
        f"PR URL value must render even when no commit artifact exists; got: {output!r}"
    )


def test_emit_completion_panel_pr_url_with_commit_artifact(tmp_path: Path) -> None:
    """PR URL is rendered alongside the commit-message artifact in the same section."""
    artifacts = tmp_path / ".agent" / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "commit_message.md").write_text(
        "---\ntype: commit\n"
        "subject: feat(display): surface polished completion output\n---\n\n"
        "## Body Summary\n\n"
        "- [S-1] Show the final commit message in the completion summary.\n",
        encoding="utf-8",
    )

    pd, buf = _display(force_terminal=True)
    snap = _make_snapshot()
    snap_with_pr = dataclasses.replace(snap, pr_url="https://example.com/pr/42")
    pd.emit_completion_summary_panel(
        snap_with_pr,
        options=CompletionSummaryOptions(workspace_root=tmp_path),
    )
    pd.stop()
    output = buf.getvalue()
    assert "https://example.com/pr/42" in output
    assert "feat(display): surface polished completion output" in output
    assert output.count("https://example.com/pr/42") == 1, (
        f"PR URL must appear exactly once in completion panel; got "
        f"{output.count('https://example.com/pr/42')} copies: {output!r}"
    )


# --- DA-003 (wt-028-display S-6 / AC-05): height-constrained completion
# panel degradation. At the canonical 12-row floor and below, the full
# Rich Group of rules/sections collapses to an unboxed condensed heading.
# The bordered layout (Plan / Metrics / Decisions / Review / Analysis /
# Iteration Context / Activity / Commit / tail / closing rule) would
# consume the entire 12-row working area; the condensed heading keeps
# the outcome + essential counts in 4 rows or fewer.
# -------------------------------------------------------------------------


def _height_aware_display(
    *, height: int, force_terminal: bool = False, width: int = 120
) -> tuple[ParallelDisplay, StringIO]:
    """Build a display whose Console carries the requested ``height``.

    Pins ``force_height=height`` on :func:`make_display_context`
    because Rich's ``Console.size.height`` does not always reflect
    the constructor's ``height`` kwarg in a non-terminal context
    (it can be ``None`` until the Console is recorded / printed).
    Pinning ``force_height`` is the documented precedence path for
    short-terminal testing.
    """
    buf = StringIO()
    console = Console(
        file=buf,
        force_terminal=force_terminal,
        color_system=("truecolor" if force_terminal else None),
        width=width,
        height=height,
    )
    ctx = make_display_context(console=console, env={}, force_height=height)
    return ParallelDisplay(ctx), buf


def test_emit_completion_panel_degrades_at_12_rows() -> None:
    """DA-003: at the canonical 12-row floor the panel collapses to a heading.

    The canonical 12-row floor is the documented accessibility path
    (large-text / magnified / braille displays); the framed
    presentation must give way to unboxed headed text there, not one
    row later. The condensed heading retains the outcome title and
    the essential counts (exit trigger + agent_calls) without the
    full Plan / Decisions / Activity / Commit rule cascade.
    """
    pd, buf = _height_aware_display(height=12)
    pd.emit_completion_summary_panel(
        _make_snapshot(),
        options=CompletionSummaryOptions(elapsed_seconds=42.0),
    )
    pd.stop()
    output = buf.getvalue()
    # Outcome title survives.
    assert "Pipeline Complete" in output, f"outcome title must survive at 12 rows; got: {output!r}"
    # Section rule still emits.
    assert "[run-completion]" in output
    # Essential counts survive.
    assert "agent_calls=4" in output, f"agent_calls count must survive at 12 rows; got: {output!r}"
    # Plan / Decisions / Activity / Commit rules are dropped (the
    # bordered layout would crowd the 12-row floor).
    assert "Plan" not in output, f"Plan rule must be dropped at 12 rows; got: {output!r}"
    assert "Decisions" not in output, f"Decisions rule must be dropped at 12 rows; got: {output!r}"
    assert "Activity" not in output, f"Activity rule must be dropped at 12 rows; got: {output!r}"
    # No panel corners.
    for corner in ("╭", "╮", "╰", "╯", "┌", "┐", "└", "┘"):
        assert corner not in output, f"panel corner {corner!r} survived at 12 rows: {output!r}"
    assert "sections condensed" in output
    assert "chars" in output
    assert "<id>" not in output
    assert ".agent/raw/unknown.rendered.log" in output


def test_short_completion_marker_counts_condensed_panel_content(tmp_path: Path) -> None:
    """The short-terminal marker measures the actual omitted panel, not its labels."""
    short_display, short_buffer = _height_aware_display(height=12)
    short_display.emit_completion_summary_panel(
        _make_snapshot(plan_summary="brief"),
        options=CompletionSummaryOptions(workspace_root=tmp_path),
    )
    short_display.stop()

    detailed_display, detailed_buffer = _height_aware_display(height=12)
    detailed_display.emit_completion_summary_panel(
        _make_snapshot(plan_summary="detailed " * 50),
        options=CompletionSummaryOptions(workspace_root=tmp_path),
    )
    detailed_display.stop()

    def condensed_size(output: str) -> int:
        marker = next(line for line in output.splitlines() if "sections condensed" in line)
        return int(marker.split(" · ")[1].split()[0])

    assert condensed_size(detailed_buffer.getvalue()) > condensed_size(short_buffer.getvalue())
    assert str(tmp_path / ".agent/raw/unknown.rendered.log") in detailed_buffer.getvalue().replace(
        "\n", ""
    )


def test_emit_completion_panel_degrades_at_11_rows() -> None:
    """DA-003: at 11 rows (one below the floor) the same condensed heading emits."""
    pd, buf = _height_aware_display(height=11)
    pd.emit_completion_summary_panel(
        _make_snapshot(),
        options=CompletionSummaryOptions(),
    )
    pd.stop()
    output = buf.getvalue()
    assert "Pipeline Complete" in output
    assert "[run-completion]" in output
    assert "agent_calls=4" in output


def test_emit_completion_panel_keeps_full_layout_at_24_rows() -> None:
    """DA-003: at height=24 the full Rich Group layout survives."""
    pd, buf = _height_aware_display(height=24)
    pd.emit_completion_summary_panel(
        _make_snapshot(),
        options=CompletionSummaryOptions(),
    )
    pd.stop()
    output = buf.getvalue()
    # The full layout has Plan, Decisions, Activity rules.
    assert "Pipeline Complete" in output
    assert "Decisions" in output, f"Decisions rule must survive at 24 rows; got: {output!r}"


def test_emit_completion_panel_failure_shows_error_line_at_12_rows() -> None:
    """DA-003: failure snapshots include the error line in the condensed heading."""
    pd, buf = _height_aware_display(height=12)
    failure = _make_snapshot(
        phase="failed",
        is_terminal_success=False,
        is_terminal_failure=True,
    )
    failure_with_error = dataclasses.replace(failure, last_error="kaboom")
    pd.emit_completion_summary_panel(
        failure_with_error,
        options=CompletionSummaryOptions(),
    )
    pd.stop()
    output = buf.getvalue()
    assert "Pipeline Failed" in output, (
        f"failure outcome title must survive at 12 rows; got: {output!r}"
    )
    assert "kaboom" in output, f"failure error line must survive at 12 rows; got: {output!r}"
