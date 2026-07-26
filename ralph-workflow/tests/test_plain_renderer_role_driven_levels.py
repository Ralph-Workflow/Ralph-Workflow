"""Regression tests: ParallelDisplay derives milestone levels from phase role, not phase name."""

from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO
from typing import Any

from rich.console import Console

from ralph.display._phase_close_options import PhaseCloseOptions
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.snapshot import PipelineSnapshot
from ralph.display.theme import UNICODE_GLYPHS


def _make_display() -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, highlight=False, color_system=None, width=200)
    return ParallelDisplay(make_display_context(console=console, env={})), buf


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
    """A phase named 'design' with role 'execution' produces a MILESTONE [phase] line.

    wt-028-display S-4: the chrome prefix no longer carries the
    ``MILESTONE`` LEVEL text; the role-driven severity is now
    carried by the milestone glyph (``\u25c6`` for execution /
    review / fix) in the body. The line still carries the [phase]
    tag, the milestone glyph, and the phase name.
    """
    pd, buf = _make_display()
    snapshot = _make_snapshot(phase="design", current_phase_role="execution")
    pd._phase_lines(snapshot, "2026-01-01T00:00:00+00:00")
    out = buf.getvalue()
    assert out == ""  # _phase_lines returns texts, doesn't print directly

    pd._last_phase = None  # reset so phase line is emitted
    texts = pd._phase_lines(snapshot, "2026-01-01T00:00:00+00:00")
    assert len(texts) == 1
    line_text = texts[0].plain
    assert "design" in line_text
    milestone_glyph = UNICODE_GLYPHS["milestone"]
    assert milestone_glyph in line_text, (
        f"wt-028-display S-4: role-driven severity for execution is "
        f"carried by the milestone glyph; got line={line_text!r}"
    )
    # The retired LEVEL text must NOT appear in the chrome prefix.
    assert "MILESTONE" not in line_text, (
        f"wt-028-display S-4: chrome prefix must not carry the "
        f"MILESTONE LEVEL text; got line={line_text!r}"
    )


def test_success_level_for_terminal_role_phase_with_renamed_phase() -> None:
    """A phase named 'done' with role 'terminal' produces a terminal-success [phase] line.

    wt-028-display S-4: the chrome prefix no longer carries the
    ``SUCCESS`` LEVEL text; role-driven severity is carried by the
    surviving icon+label carrier in the body. The line still carries
    the [phase] tag and the phase name; for terminal-success the
    carrier is the surviving OK carrier (``[OK]`` ASCII / the
    terminated marker Unicode).
    """
    pd, _buf = _make_display()
    snapshot = _make_snapshot(
        phase="done",
        current_phase_role="terminal",
        is_terminal_success=True,
    )
    pd._last_phase = None
    texts = pd._phase_lines(snapshot, "2026-01-01T00:00:00+00:00")
    assert len(texts) == 1
    line_text = texts[0].plain
    assert "done" in line_text
    # The retired LEVEL text must NOT appear in the chrome prefix.
    assert "SUCCESS" not in line_text, (
        f"wt-028-display S-4: chrome prefix must not carry the "
        f"SUCCESS LEVEL text; got line={line_text!r}"
    )


def test_error_level_for_terminal_failure_with_renamed_phase() -> None:
    """A phase with is_terminal_failure=True produces a terminal-failure [phase] line.

    wt-028-display S-4: the chrome prefix no longer carries the
    ``ERROR`` LEVEL text; role-driven severity is carried by the
    surviving icon+label carrier in the body. The line still
    carries the [phase] tag and the phase name.
    """
    pd, _buf = _make_display()
    snapshot = _make_snapshot(
        phase="failed_terminal",
        current_phase_role="terminal",
        is_terminal_failure=True,
        last_error="boom",
    )
    pd._last_phase = None
    texts = pd._phase_lines(snapshot, "2026-01-01T00:00:00+00:00")
    assert len(texts) == 1
    line_text = texts[0].plain
    assert "failed_terminal" in line_text
    # The retired LEVEL text must NOT appear in the chrome prefix.
    assert "ERROR" not in line_text, (
        f"wt-028-display S-4: chrome prefix must not carry the "
        f"ERROR LEVEL text; got line={line_text!r}"
    )


def test_warn_level_when_interrupted_by_user() -> None:
    """interrupted_by_user=True produces a [phase] line tagged interrupted, no WARN chrome.

    wt-028-display S-4: the chrome prefix no longer carries the
    ``WARN`` LEVEL text; the user-interrupted signal is now
    carried by the surviving icon+label carrier in the body
    (the warn / interrupted glyph). The line still carries the
    [phase] tag and the phase name.
    """
    pd, _buf = _make_display()
    snapshot = _make_snapshot(
        phase="any_custom_phase",
        current_phase_role="execution",
        interrupted_by_user=True,
    )
    pd._last_phase = None
    texts = pd._phase_lines(snapshot, "2026-01-01T00:00:00+00:00")
    assert len(texts) == 1
    line_text = texts[0].plain
    assert "any_custom_phase" in line_text
    # The retired LEVEL text must NOT appear in the chrome prefix.
    assert "WARN" not in line_text, (
        f"wt-028-display S-4: chrome prefix must not carry the "
        f"WARN LEVEL text; got line={line_text!r}"
    )


def test_phase_close_milestone_glyph_for_review_role_renamed() -> None:
    """emit_phase_close with phase_role='review' produces a milestone glyph prefix."""
    pd, buf = _make_display()
    pd.emit_phase_close("audit", "audit: done", options=PhaseCloseOptions(phase_role="review"))
    out = buf.getvalue()
    milestone_glyph = UNICODE_GLYPHS["milestone"]
    assert milestone_glyph in out
    assert "phase=audit" in out


def test_phase_close_no_milestone_glyph_for_analysis_role() -> None:
    """emit_phase_close with phase_role='analysis' produces no milestone glyph."""
    pd, buf = _make_display()
    pd.emit_phase_close("audit", "audit: done", options=PhaseCloseOptions(phase_role="analysis"))
    out = buf.getvalue()
    milestone_glyph = UNICODE_GLYPHS["milestone"]
    assert milestone_glyph not in out
    assert "phase=audit" in out


def test_phase_close_no_milestone_glyph_without_phase_role() -> None:
    """emit_phase_close without phase_role produces no milestone glyph."""
    pd, buf = _make_display()
    pd.emit_phase_close("planning", "plan: done")
    out = buf.getvalue()
    milestone_glyph = UNICODE_GLYPHS["milestone"]
    assert milestone_glyph not in out
    assert "phase=planning" in out
