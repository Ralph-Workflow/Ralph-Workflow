"""ANSI-vs-plain regression tests for completion_summary decision badge rendering."""

from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO

from rich.console import Console

from ralph.display.completion_summary import _make_badge_text, emit_completion_summary
from ralph.display.context import make_display_context
from ralph.display.snapshot import PipelineSnapshot
from ralph.display.theme import RALPH_THEME


def _make_snapshot() -> PipelineSnapshot:
    return PipelineSnapshot(
        phase="complete",
        previous_phase=None,
        review_issues_found=False,
        interrupted_by_user=False,
        last_error=None,
        pr_url=None,
        push_count=0,
        total_agent_calls=3,
        total_continuations=0,
        total_fallbacks=0,
        total_retries=0,
        workers=(),
        prompt_path="PROMPT.md",
        prompt_preview=(),
        run_id="r1",
        created_at=datetime(2026, 4, 21, tzinfo=UTC),
        plan_summary="Test plan",
        plan_scope_items=("item A",),
        plan_total_steps=1,
        plan_current_step=1,
        plan_risks=(),
        decision_log=(
            ("development_analysis", "proceed", "tests green", "2026-04-21T00:00:00+00:00"),
            ("review_analysis", "revise", "nit", "2026-04-21T00:01:00+00:00"),
            ("review_analysis", "failed", "error", "2026-04-21T00:02:00+00:00"),
        ),
    )


def _themed_context(buf: StringIO) -> object:
    """Create a DisplayContext for themed (color) output."""
    console = Console(
        file=buf,
        force_terminal=True,
        color_system="truecolor",
        theme=RALPH_THEME,
        width=200,
        highlight=False,
    )
    return make_display_context(console=console, env={})


def _plain_context(buf: StringIO) -> object:
    """Create a DisplayContext for plain (no color) output."""
    console = Console(
        file=buf,
        force_terminal=False,
        color_system=None,
        width=200,
    )
    return make_display_context(console=console, env={})


def test_badges_emit_ansi_on_tty() -> None:
    buf = StringIO()
    ctx = _themed_context(buf)
    emit_completion_summary(_make_snapshot(), display_context=ctx)
    assert "\x1b[" in buf.getvalue()


def test_badges_no_ansi_on_plain() -> None:
    buf = StringIO()
    ctx = _plain_context(buf)
    emit_completion_summary(_make_snapshot(), display_context=ctx)
    out = buf.getvalue()
    assert "\x1b[" not in out


def test_pass_badge_label_present_on_plain() -> None:
    buf = StringIO()
    ctx = _plain_context(buf)
    emit_completion_summary(_make_snapshot(), display_context=ctx)
    assert "[PASS]" in buf.getvalue()


def test_warn_badge_label_present_on_plain() -> None:
    buf = StringIO()
    ctx = _plain_context(buf)
    emit_completion_summary(_make_snapshot(), display_context=ctx)
    assert "[WARN]" in buf.getvalue()


def test_fail_badge_label_present_on_plain() -> None:
    buf = StringIO()
    ctx = _plain_context(buf)
    emit_completion_summary(_make_snapshot(), display_context=ctx)
    assert "[FAIL]" in buf.getvalue()


def test_badge_reason_text_dim_in_themed_output() -> None:
    """_make_badge_text applies dim muted style to reason text in themed output."""
    buf = StringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        color_system="truecolor",
        theme=RALPH_THEME,
        width=200,
        highlight=False,
    )
    t = _make_badge_text("PASS", " Development Analysis: proceed")
    console.print(t, markup=False, highlight=False, no_wrap=True)
    out = buf.getvalue()
    assert "Development Analysis: proceed" in out
    # dim ANSI code (\x1b[2m) must be present since theme.text.muted = 'dim'
    assert "\x1b[2m" in out
