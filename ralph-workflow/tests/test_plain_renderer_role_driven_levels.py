"""Regression tests: plain_renderer derives milestone levels from phase role, not phase name."""

from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO
from typing import Any

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.plain_renderer import PhaseCloseOptions, PlainLogRenderer
from ralph.display.snapshot import PipelineSnapshot
from ralph.display.theme import UNICODE_GLYPHS


def _make_renderer() -> tuple[PlainLogRenderer, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False, color_system=None, width=200)
    return PlainLogRenderer(make_display_context(console=console, env={})), buf


def _make_snapshot(**kwargs: object) -> PipelineSnapshot:
    defaults: dict[str, Any] = {
        "phase": "design",
        "previous_phase": None,
        "review_issues_found": False,
        "interrupted_by_user": False,
        "last_error": None,
        "pr_url": None,
        "push_count": 0,
        "total_agent_calls": 1,
        "total_continuations": 0,
        "total_fallbacks": 0,
        "total_retries": 0,
        "workers": (),
        "prompt_path": None,
        "prompt_preview": (),
        "run_id": "test-run",
        "created_at": datetime(2026, 4, 30, 12, 0, tzinfo=UTC),
        "decision_log": (),
        "is_terminal_failure": False,
        "is_terminal_success": False,
        "current_phase_role": "execution",
        "previous_phase_role": None,
        "terminal_failure_route": None,
    }
    defaults.update(kwargs)
    return PipelineSnapshot(**defaults)


def test_milestone_level_for_execution_role_phase_with_renamed_phase() -> None:
    """A phase named 'design' with role 'execution' produces a MILESTONE [phase] line."""
    renderer, buf = _make_renderer()
    snapshot = _make_snapshot(phase="design", current_phase_role="execution")
    renderer._phase_lines(snapshot, "2026-01-01T00:00:00+00:00")
    out = buf.getvalue()
    assert out == ""  # _phase_lines returns texts, doesn't print directly

    # Call via emit_snapshot to trigger actual output
    buf.truncate(0)
    buf.seek(0)
    renderer._last_phase = None  # reset so phase line is emitted
    texts = renderer._phase_lines(snapshot, "2026-01-01T00:00:00+00:00")
    assert len(texts) == 1
    line_text = texts[0].plain
    assert "MILESTONE" in line_text
    assert "design" in line_text
    milestone_glyph = UNICODE_GLYPHS["milestone"]
    assert milestone_glyph in line_text


def test_success_level_for_terminal_role_phase_with_renamed_phase() -> None:
    """A phase named 'done' with role 'terminal' produces a SUCCESS [phase] line."""
    renderer, _buf = _make_renderer()
    snapshot = _make_snapshot(
        phase="done",
        current_phase_role="terminal",
        is_terminal_success=True,
    )
    renderer._last_phase = None
    texts = renderer._phase_lines(snapshot, "2026-01-01T00:00:00+00:00")
    assert len(texts) == 1
    line_text = texts[0].plain
    assert "SUCCESS" in line_text
    assert "done" in line_text


def test_error_level_for_terminal_failure_with_renamed_phase() -> None:
    """A phase with is_terminal_failure=True produces an ERROR [phase] line."""
    renderer, _buf = _make_renderer()
    snapshot = _make_snapshot(
        phase="failed_terminal",
        current_phase_role="terminal",
        is_terminal_failure=True,
        last_error="boom",
    )
    renderer._last_phase = None
    texts = renderer._phase_lines(snapshot, "2026-01-01T00:00:00+00:00")
    assert len(texts) == 1
    line_text = texts[0].plain
    assert "ERROR" in line_text
    assert "failed_terminal" in line_text


def test_warn_level_when_interrupted_by_user() -> None:
    """interrupted_by_user=True produces a WARN [phase] line regardless of phase name."""
    renderer, _buf = _make_renderer()
    snapshot = _make_snapshot(
        phase="any_custom_phase",
        current_phase_role="execution",
        interrupted_by_user=True,
    )
    renderer._last_phase = None
    texts = renderer._phase_lines(snapshot, "2026-01-01T00:00:00+00:00")
    assert len(texts) == 1
    line_text = texts[0].plain
    assert "WARN" in line_text
    assert "any_custom_phase" in line_text


def test_phase_close_milestone_glyph_for_review_role_renamed() -> None:
    """emit_phase_close with phase_role='review' produces a milestone glyph prefix."""
    renderer, buf = _make_renderer()
    renderer.emit_phase_close(
        "audit", "audit: done", options=PhaseCloseOptions(phase_role="review")
    )
    out = buf.getvalue()
    milestone_glyph = UNICODE_GLYPHS["milestone"]
    assert milestone_glyph in out
    assert "phase=audit" in out


def test_phase_close_no_milestone_glyph_for_analysis_role() -> None:
    """emit_phase_close with phase_role='analysis' produces no milestone glyph."""
    renderer, buf = _make_renderer()
    renderer.emit_phase_close(
        "audit", "audit: done", options=PhaseCloseOptions(phase_role="analysis")
    )
    out = buf.getvalue()
    milestone_glyph = UNICODE_GLYPHS["milestone"]
    assert milestone_glyph not in out
    assert "phase=audit" in out


def test_phase_close_no_milestone_glyph_without_phase_role() -> None:
    """emit_phase_close without phase_role produces no milestone glyph."""
    renderer, buf = _make_renderer()
    renderer.emit_phase_close("planning", "plan: done")
    out = buf.getvalue()
    milestone_glyph = UNICODE_GLYPHS["milestone"]
    assert milestone_glyph not in out
    assert "phase=planning" in out
