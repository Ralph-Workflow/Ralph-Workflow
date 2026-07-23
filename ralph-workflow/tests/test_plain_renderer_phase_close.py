"""Tests for ParallelDisplay.emit_phase_close (consolidated from PlainLogRenderer)."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display._phase_close_options import PhaseCloseOptions
from ralph.display._plain_constants import TAG_CATEGORY, TAGS
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.phase_status import PhaseIterationContext
from ralph.display.theme import UNICODE_GLYPHS


def _make_display() -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False, color_system=None, width=200)
    return ParallelDisplay(make_display_context(console=console, env={})), buf


def test_phase_close_emits_body_line() -> None:
    """emit_phase_close emits a single body line (plus a section rule)."""
    pd, buf = _make_display()
    pd.emit_phase_close(
        "planning", "plan: 5 step(s), 2 risk(s)", options=PhaseCloseOptions(phase_role="execution")
    )
    out = buf.getvalue()
    body_lines = [ln for ln in out.splitlines() if "[phase-close]" in ln and "───" not in ln]
    assert len(body_lines) == 1
    milestone = UNICODE_GLYPHS["milestone"]
    expected = f"INFO META [phase-close] {milestone} phase=planning plan: 5 step(s), 2 risk(s)"
    assert expected in body_lines[0]


def test_phase_close_flushes_open_streaming_block() -> None:
    pd, buf = _make_display()
    # Open a streaming block
    pd.emit_activity_line("u", "text", "streaming content")
    pd.emit_phase_close("development", "development: result artifact present")
    out = buf.getvalue()
    # [content-end] must appear before the body [phase-close] line
    body_phase_close_idx = out.index("INFO META [phase-close] phase=development")
    content_end_idx = out.index("[content-end]")
    assert "[content-end]" in out
    assert content_end_idx < body_phase_close_idx, (
        f"[content-end] (at {content_end_idx}) must appear before "
        f"the [phase-close] body line (at {body_phase_close_idx}); got:\n{out}"
    )


def test_phase_close_reduces_rich_markup() -> None:
    pd, buf = _make_display()
    pd.emit_phase_close("review", "[red]issues[/red]")
    out = buf.getvalue()
    assert "phase=review issues" in out
    assert "[red]" not in out


def test_phase_close_no_ansi() -> None:
    pd, buf = _make_display()
    pd.emit_phase_close("planning", "plan: 3 step(s), 1 risk(s)")
    assert "\x1b[" not in buf.getvalue()


def test_phase_close_tag_is_registered() -> None:
    assert "phase-close" in TAGS
    assert TAG_CATEGORY.get("phase-close") == "META"


def test_phase_close_trims_empty_produced() -> None:
    pd, buf = _make_display()
    pd.emit_phase_close("planning", "", options=PhaseCloseOptions(phase_role="execution"))
    out = buf.getvalue()
    body_lines = [ln for ln in out.splitlines() if "[phase-close]" in ln and "───" not in ln]
    assert len(body_lines) == 1
    line = body_lines[0]
    milestone = UNICODE_GLYPHS["milestone"]
    assert f"{milestone} phase=planning" in line
    suffix = "(elapsed=0.0s, content_blocks=0, thinking_blocks=0, tool_calls=0, errors=0)"
    assert line.endswith(suffix), f"expected line to end with {suffix}, got: {line}"


def test_phase_close_counts_tool_results_as_tool_calls() -> None:
    pd, buf = _make_display()
    pd.begin_phase("development_commit")
    pd.emit_activity_line("u", "tool_result", "tool finished")
    pd.emit_phase_close("development_commit", "")
    out = buf.getvalue()
    assert "tool_calls=1" in out
    assert "errors=0" in out


def test_phase_close_non_milestone_role_has_no_glyph() -> None:
    """Non-milestone roles (terminal, analysis) emit no glyph prefix."""
    pd, buf = _make_display()
    pd.emit_phase_close("done", "result: done", options=PhaseCloseOptions(phase_role="terminal"))
    out = buf.getvalue()
    assert "[phase-close] phase=done result: done" in out
    assert UNICODE_GLYPHS["milestone"] not in out


def test_phase_close_milestone_role_ascii_glyph() -> None:
    """RALPH_FORCE_ASCII=1 emits ASCII milestone glyph (* not ◆) for milestone roles."""
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False, color_system=None, width=200)
    pd = ParallelDisplay(make_display_context(console=console, env={"RALPH_FORCE_ASCII": "1"}))
    pd.emit_phase_close(
        "planning", "plan: 3 step(s)", options=PhaseCloseOptions(phase_role="execution")
    )
    out = buf.getvalue()
    assert "[phase-close] * phase=planning" in out
    assert UNICODE_GLYPHS["milestone"] not in out


def test_phase_close_iteration_context_labels_appear_after_phase_name() -> None:
    """emit_phase_close with iteration_context includes canonical labels in output."""
    pd, buf = _make_display()
    ctx = PhaseIterationContext(outer_dev=2, inner_analysis=1)
    pd.emit_phase_close("fix", "fix: applied", options=PhaseCloseOptions(iteration_context=ctx))
    out = buf.getvalue()
    assert "phase=fix" in out
    assert "[Cycle #2]" in out
    assert "[Analysis #1]" in out
    assert "fix: applied" in out


def test_phase_close_iteration_context_none_no_labels() -> None:
    """emit_phase_close without iteration_context emits no canonical label brackets."""
    pd, buf = _make_display()
    pd.emit_phase_close("planning", "plan: done")
    out = buf.getvalue()
    assert "[Dev #" not in out
    assert "[Fixer #" not in out
    assert "[Analysis" not in out


def test_phase_close_iteration_context_empty_no_labels() -> None:
    """emit_phase_close with empty PhaseIterationContext emits no extra brackets."""
    pd, buf = _make_display()
    opts = PhaseCloseOptions(iteration_context=PhaseIterationContext())
    pd.emit_phase_close("planning", "plan: done", options=opts)
    out = buf.getvalue()
    assert "[Dev #" not in out
    assert "[Fixer #" not in out


def test_phase_close_iteration_context_analysis_with_cap() -> None:
    """emit_phase_close with inner_analysis + cap shows [Analysis N/cap] label."""
    pd, buf = _make_display()
    ctx = PhaseIterationContext(inner_analysis=3, inner_analysis_cap=5)
    pd.emit_phase_close(
        "development_analysis", "analysis: done", options=PhaseCloseOptions(iteration_context=ctx)
    )
    out = buf.getvalue()
    assert "[Analysis 3/5]" in out


def test_phase_close_exit_trigger_included_in_output() -> None:
    """emit_phase_close with exit_trigger includes 'exit=<trigger>' in the output line."""
    pd, buf = _make_display()
    pd.emit_phase_close(
        "development", "dev: result", options=PhaseCloseOptions(exit_trigger="produced")
    )
    out = buf.getvalue()
    assert "exit=produced" in out


def test_phase_close_exit_trigger_none_omits_exit_field() -> None:
    """emit_phase_close with exit_trigger=None emits no 'exit=' field."""
    pd, buf = _make_display()
    pd.emit_phase_close("development", "dev: result")
    out = buf.getvalue()
    assert "exit=" not in out


def test_phase_close_exit_trigger_appears_before_elapsed() -> None:
    """exit=<trigger> should appear before the elapsed/counter block."""
    pd, buf = _make_display()
    pd.emit_phase_close(
        "planning", "plan: done", options=PhaseCloseOptions(exit_trigger="produced")
    )
    out = buf.getvalue()
    assert "exit=produced" in out
    assert out.index("exit=produced") < out.index("elapsed=")


def test_phase_close_exit_trigger_with_iteration_context() -> None:
    """exit_trigger and iteration_context can coexist on the same phase-close line."""
    pd, buf = _make_display()
    ctx = PhaseIterationContext(outer_dev=2)
    opts = PhaseCloseOptions(iteration_context=ctx, exit_trigger="produced")
    pd.emit_phase_close("fix", "fix: applied", options=opts)
    out = buf.getvalue()
    assert "[Cycle #2]" in out
    assert "exit=produced" in out
    assert "fix: applied" in out


def test_phase_close_exit_trigger_does_not_render_as_markup() -> None:
    """exit_trigger is printed with markup=False so Rich markup appears literally."""
    pd, buf = _make_display()
    # Plain string exit trigger is shown as-is
    pd.emit_phase_close(
        "planning", "plan: done", options=PhaseCloseOptions(exit_trigger="produced")
    )
    out = buf.getvalue()
    assert "exit=produced" in out
