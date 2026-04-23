"""ANSI-vs-plain regression tests for completion_summary decision badge rendering."""

from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO

from rich.console import Console

from ralph.display.completion_summary import emit_completion_summary
from ralph.display.snapshot import PipelineSnapshot
from ralph.display.theme import RALPH_THEME


def _make_snapshot() -> PipelineSnapshot:
    return PipelineSnapshot(
        phase="complete",
        previous_phase=None,
        iteration=1,
        total_iterations=2,
        reviewer_pass=0,
        total_reviewer_passes=1,
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


def test_badges_emit_ansi_on_tty() -> None:
    buf = StringIO()
    c = Console(
        file=buf,
        force_terminal=True,
        color_system="truecolor",
        theme=RALPH_THEME,
        width=200,
        highlight=False,
    )
    emit_completion_summary(c, _make_snapshot())
    assert "\x1b[" in buf.getvalue()


def test_badges_no_ansi_on_plain() -> None:
    buf = StringIO()
    c = Console(
        file=buf,
        force_terminal=False,
        color_system=None,
        width=200,
    )
    emit_completion_summary(c, _make_snapshot())
    out = buf.getvalue()
    assert "\x1b[" not in out


def test_pass_badge_label_present_on_plain() -> None:
    buf = StringIO()
    c = Console(file=buf, force_terminal=False, color_system=None, width=200)
    emit_completion_summary(c, _make_snapshot())
    assert "[PASS]" in buf.getvalue()


def test_warn_badge_label_present_on_plain() -> None:
    buf = StringIO()
    c = Console(file=buf, force_terminal=False, color_system=None, width=200)
    emit_completion_summary(c, _make_snapshot())
    assert "[WARN]" in buf.getvalue()


def test_fail_badge_label_present_on_plain() -> None:
    buf = StringIO()
    c = Console(file=buf, force_terminal=False, color_system=None, width=200)
    emit_completion_summary(c, _make_snapshot())
    assert "[FAIL]" in buf.getvalue()
