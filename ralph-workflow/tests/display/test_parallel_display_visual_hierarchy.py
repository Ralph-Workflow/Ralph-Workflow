"""Visual-hierarchy regression tests for ParallelDisplay.

This file pins the visual-hierarchy contract for the run-start/phase-close/run-end
section rules and banner titles:

  AC-01: section rule line carries the theme.banner.border style (sky-blue).
  AC-02: run-start and run-end banner titles carry the theme.banner.title style.
  AC-03: a blank line appears immediately before every section rule and after
         the run-end block.
  AC-04: TTY and non-TTY renders produce the same plain text (modulo ANSI/glyph).
  AC-05: log-line tag markers [run-start], [phase-close], [run-end], [unit_id]
         are preserved byte-for-byte.

Tests (a)-(c) are the TDD-red tests that drive the visual-hierarchy refactor.
Tests (d) and (e) are anti-drift guards that pass on the current code and must
stay green after the refactor.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from rich.console import Console

from ralph.display._run_start_orientation import RunStartOrientation
from ralph.display.completion_summary import CompletionSummaryOptions
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.snapshot import PipelineSnapshot
from ralph.display.theme import RALPH_THEME

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?[+\-]\d{2}:\d{2}")
# Wall-clock-based `elapsed=Ns` substring (e.g. `elapsed=0.0s`, `elapsed=12.3s`).
# `ParallelDisplay` reads real `time.monotonic()` and rounds to 1 decimal; the
# two display paths can emit a different rounded value when the system clock
# is under load (e.g. `0.0s` on the TTY path, `0.1s` on the non-TTY path).
# Normalizing the substring makes the parity assertion deterministic without
# forcing the production code to inject a clock.
_ELAPSED_RE = re.compile(r"elapsed=\d+(?:\.\d+)?s")
# Pre-built ANSI prefix for theme.banner.border (#56B4E9 = sky-blue). When Rich
# applies a hex color to a Text span it emits \x1b[38;2;86;180;233m (24-bit
# truecolor) on a `truecolor` color_system console.
_SKY_BLUE_24BIT_ANSI = "\x1b[38;2;86;180;233m"
# Pre-built ANSI prefix for bold (theme.banner.title wraps `bold #56B4E9`).
# The escape `\x1b[1;...m` starts with `\x1b[1` (bold attribute) before the
# color code; we match the bold attribute alone.
_BOLD_ATTR_ANSI = "\x1b[1"
# Plain-text marker of the section rule line.
_SECTION_RULE_MARKER = "───"


def _make_display(*, force_terminal: bool) -> tuple[ParallelDisplay, io.StringIO]:
    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=force_terminal,
        color_system=("truecolor" if force_terminal else None),
        width=120,
        theme=RALPH_THEME,
    )
    ctx = make_display_context(console=console, env={"CI": "1"})
    return ParallelDisplay(ctx), buf


def _run_lifecycle(pd: ParallelDisplay) -> None:
    pd.emit_run_start(RunStartOrientation())
    pd.begin_phase("planning")
    pd.emit_phase_close("planning", "artifacts")
    pd.emit_run_end(phase="final")
    pd.emit(unit_id="unit-1", line="hello world")


def _section_rule_line(raw: str) -> str | None:
    """Return the first non-empty line that contains the section rule glyph."""
    for line in raw.splitlines():
        if "─" in line and "[run-start]" in line:
            return line
    return None


def test_section_rule_uses_banner_border_style() -> None:
    """AC-01: the section rule line carries the theme.banner.border sky-blue ANSI."""
    pd, buf = _make_display(force_terminal=True)
    _run_lifecycle(pd)
    text = buf.getvalue()
    rule_line = _section_rule_line(text)
    assert rule_line is not None, f"section rule line not found in:\n{text!r}"
    assert _SKY_BLUE_24BIT_ANSI in rule_line, (
        f"section rule line must carry the theme.banner.border sky-blue ANSI "
        f"({_SKY_BLUE_24BIT_ANSI!r}); got:\n{rule_line!r}"
    )
    assert "─" in rule_line, f"section rule line must contain the rule glyph; got:\n{rule_line!r}"


def test_banner_title_uses_theme_banner_title_style() -> None:
    """AC-02: the run-start/run-end banner titles carry the bold theme.banner.title style."""
    pd, buf = _make_display(force_terminal=True)
    _run_lifecycle(pd)
    text = buf.getvalue()
    run_start_line = next(
        (line for line in text.splitlines() if "Ralph Workflow run start" in line), None
    )
    run_end_line = next(
        (line for line in text.splitlines() if "Ralph Workflow run end" in line), None
    )
    assert run_start_line is not None, f"run-start banner title line not found in:\n{text!r}"
    assert run_end_line is not None, f"run-end banner title line not found in:\n{text!r}"
    assert _BOLD_ATTR_ANSI in run_start_line, (
        f"run-start banner title must carry the bold theme.banner.title style "
        f"({_BOLD_ATTR_ANSI!r}); got:\n{run_start_line!r}"
    )
    assert _BOLD_ATTR_ANSI in run_end_line, (
        f"run-end banner title must carry the bold theme.banner.title style "
        f"({_BOLD_ATTR_ANSI!r}); got:\n{run_end_line!r}"
    )


def test_blank_line_before_and_after_section_rule() -> None:
    """AC-03: blank line before every section rule; blank line after the run-end block."""
    pd, buf = _make_display(force_terminal=True)
    _run_lifecycle(pd)
    text = buf.getvalue()
    # A blank line immediately before the section rule line means a `\n\n`
    # before the rule glyph. We strip ANSI and check the structure on
    # plain text.
    plain = _ANSI_ESCAPE_RE.sub("", text)
    for tag in ("[run-start]", "[phase-close]", "[run-end]"):
        rule_marker = f"─── {tag}"
        rule_idx = plain.find(rule_marker)
        assert rule_idx > 0, f"section rule for {tag!r} not found in:\n{plain!r}"
        # Check that there's a blank line (two consecutive newlines) between the
        # previous content and the section rule. For the first section rule at
        # the start of the buffer, only the leading `\n` from the blank-line
        # call precedes it.
        if plain[:rule_idx].strip("\n"):
            assert plain[rule_idx - 1] == "\n" and plain[rule_idx - 2] == "\n", (
                f"section rule for {tag!r} must be preceded by a blank line; "
                f"got chars at idx {rule_idx - 2}:{rule_idx}: "
                f"{plain[rule_idx - 2 : rule_idx]!r}\n"
                f"full plain text:\n{plain!r}"
            )
    # The blank line after the run-end block is followed by the
    # [content][unit-1] emit. Verify that a `\n\n` (blank line) appears
    # between the last run-end section rule block and the next emit.
    last_run_end_rule = plain.rfind("─── [run-end]")
    assert last_run_end_rule > 0, f"last run-end section rule not found in:\n{plain!r}"
    after_run_end_block = plain[last_run_end_rule:]
    # The blank line check: a `\n\n` (blank line) anywhere after the last
    # run-end section rule. This is the visual closing punctuation emitted
    # by the run-end block.
    assert "\n\n" in after_run_end_block, (
        f"expected a blank line after the run-end block; "
        f"got plain text after run-end section rule:\n{after_run_end_block!r}"
    )


def test_visual_hierarchy_parity_tty_vs_non_tty() -> None:
    """AC-04: TTY and non-TTY renders produce the same plain text.

    Anti-drift guard: passes on current code; would fail if visual-hierarchy changes
    break TTY/non-TTY parity.
    """
    pd_tty, buf_tty = _make_display(force_terminal=True)
    pd_no_tty, buf_no_tty = _make_display(force_terminal=False)
    _run_lifecycle(pd_tty)
    _run_lifecycle(pd_no_tty)
    text_tty = _ELAPSED_RE.sub(
        "elapsed=<T>",
        _TIMESTAMP_RE.sub("<TS>", _ANSI_ESCAPE_RE.sub("", buf_tty.getvalue())),
    )
    text_no_tty = _ELAPSED_RE.sub(
        "elapsed=<T>",
        _TIMESTAMP_RE.sub("<TS>", _ANSI_ESCAPE_RE.sub("", buf_no_tty.getvalue())),
    )
    assert text_tty == text_no_tty, (
        "TTY and non-TTY output must match modulo ANSI escape codes.\n"
        f"--- TTY ---\n{text_tty!r}\n--- non-TTY ---\n{text_no_tty!r}\n"
    )


def test_visual_hierarchy_log_tag_markers_preserved() -> None:
    """AC-05: log-line tag markers are preserved byte-for-byte.

    Anti-drift guard: passes on current code; would fail if visual-hierarchy changes
    drop log-line tag markers.
    """
    pd, buf = _make_display(force_terminal=True)
    _run_lifecycle(pd)
    text = buf.getvalue()
    assert "[run-start]" in text, f"[run-start] marker missing from:\n{text!r}"
    assert "[phase-close]" in text, f"[phase-close] marker missing from:\n{text!r}"
    assert "[run-end]" in text, f"[run-end] marker missing from:\n{text!r}"
    assert "unit-1" in text, f"unit_id 'unit-1' missing from:\n{text!r}"


# ---------------------------------------------------------------------------
# wt-007: visual-hierarchy cases for the 5 new emit_* methods.
# Each new method must (1) emit a section-rule header in non-compact mode,
# (2) carry the theme.banner.title style, (3) use theme.text.muted for body
# cells, and (4) be markup-free in plain output.
# ---------------------------------------------------------------------------


def test_visual_hierarchy_emit_metrics_table() -> None:
    pd, buf = _make_display(force_terminal=True)
    pd.emit_metrics_table({"files_touched": 7})
    pd.stop()
    text = buf.getvalue()
    assert "[metrics]" in text, f"missing section rule: {text!r}"
    assert "Pipeline Metrics" in text, f"missing banner title: {text!r}"
    assert "files_touched" in text, f"missing body cell: {text!r}"
    assert _BOLD_ATTR_ANSI in text, f"missing bold banner title style: {text!r}"


def test_visual_hierarchy_emit_checkpoint_summary_table() -> None:
    @dataclass
    class _Opts:
        phase: str = "planning"
        budget_progress: dict[str, tuple[int, int]] | None = None

        def __post_init__(self) -> None:
            if self.budget_progress is None:
                self.budget_progress = {"iterations": (1, 3)}

    buf2 = io.StringIO()
    console2 = Console(
        file=buf2,
        force_terminal=True,
        color_system="truecolor",
        width=120,
        theme=RALPH_THEME,
    )
    pd2 = ParallelDisplay(make_display_context(console=console2, env={}))
    pd2.emit_checkpoint_summary_table(_Opts())
    pd2.stop()
    text = buf2.getvalue()
    assert "[checkpoint]" in text, f"missing section rule: {text!r}"
    assert "Checkpoint Summary" in text, f"missing banner title: {text!r}"
    assert "Phase" in text, f"missing body cell: {text!r}"


def test_visual_hierarchy_emit_blank_line() -> None:
    pd, buf = _make_display(force_terminal=False)
    pd.emit_blank_line()
    pd.stop()
    text = buf.getvalue()
    assert text == "\n", f"expected single newline, got: {text!r}"


def test_visual_hierarchy_emit_dry_run_summary() -> None:
    pd, buf = _make_display(force_terminal=True)
    pd.emit_dry_run_summary(phase="development", iterations=3)
    pd.stop()
    text = buf.getvalue()
    assert "[dry-run]" in text, f"missing section rule: {text!r}"
    assert "Dry run mode" in text, f"missing header: {text!r}"
    assert "development" in text, f"missing phase body: {text!r}"


def test_visual_hierarchy_emit_info_panel() -> None:
    pd, buf = _make_display(force_terminal=True)
    pd.emit_info_panel(title="Next steps", content="  \u2022 Run ralph --init")
    pd.stop()
    text = buf.getvalue()
    assert "Next steps" in text, f"missing panel title: {text!r}"
    assert "Run ralph --init" in text, f"missing panel content: {text!r}"


def test_visual_hierarchy_emit_completion_summary_panel() -> None:
    """AC-01+AC-03: emit_completion_summary_panel emits the [run-completion] section rule.

    Wide mode: the body itself begins with a titled Rule
    ("Pipeline Complete" or "Pipeline Failed"). The section rule line carries
    the theme.banner.border sky-blue ANSI prefix. Both the section rule and
    the body title are present (the wide-mode double-rule is intentional
    visual punctuation).
    """

    def _make_snapshot() -> PipelineSnapshot:
        return PipelineSnapshot(
            phase="complete",
            previous_phase=None,
            review_issues_found=False,
            interrupted_by_user=False,
            last_error=None,
            pr_url=None,
            push_count=1,
            total_agent_calls=4,
            total_continuations=1,
            total_fallbacks=0,
            total_retries=0,
            workers=(),
            prompt_path="PROMPT.md",
            prompt_preview=(),
            run_id="r1",
            created_at=datetime(2026, 4, 21, tzinfo=UTC),
            plan_summary="Build the feature",
            plan_scope_items=("item A",),
            plan_total_steps=2,
            plan_current_step=2,
            plan_risks=(),
            decision_log=(),
            is_terminal_success=True,
            is_terminal_failure=False,
        )

    pd, buf = _make_display(force_terminal=True)
    pd.emit_completion_summary_panel(
        _make_snapshot(),
        options=CompletionSummaryOptions(),
    )
    pd.stop()
    text = buf.getvalue()
    assert "[run-completion]" in text, f"missing [run-completion] section rule: {text!r}"
    assert "Pipeline Complete" in text, f"missing body title: {text!r}"
    assert _SKY_BLUE_24BIT_ANSI in text, (
        f"section rule line must carry the theme.banner.border sky-blue ANSI "
        f"({_SKY_BLUE_24BIT_ANSI!r}); got:\n{text!r}"
    )
