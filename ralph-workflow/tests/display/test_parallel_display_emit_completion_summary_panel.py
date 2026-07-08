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
import json
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
    artifacts = tmp_path / ".agent" / "tmp"
    artifacts.mkdir(parents=True)
    (artifacts / "commit_message.json").write_text(
        json.dumps(
            {
                "name": "commit_message",
                "type": "commit_message",
                "content": {
                    "type": "commit",
                    "subject": "feat(display): surface polished completion output",
                    "body_summary": "Show the final commit message in the completion summary.",
                },
                "created_at": "STATIC",
                "updated_at": "STATIC",
                "metadata": {},
            }
        ),
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
    artifacts = tmp_path / ".agent" / "tmp"
    artifacts.mkdir(parents=True)
    (artifacts / "commit_message.json").write_text(
        json.dumps(
            {
                "name": "commit_message",
                "type": "commit_message",
                "content": {
                    "type": "commit",
                    "subject": "feat(display): surface polished completion output",
                    "body_summary": "Show the final commit message in the completion summary.",
                },
                "created_at": "STATIC",
                "updated_at": "STATIC",
                "metadata": {},
            }
        ),
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
