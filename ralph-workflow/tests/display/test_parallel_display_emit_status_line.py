"""Black-box tests for ``ParallelDisplay.emit_status_line`` (wt-028-display).

Pins the public emit method that consolidates per-unit status lines
onto ``ParallelDisplay`` (closing the free-function ``_console.out(...)``
bypass in ``ralph/display/plain_renderer/_plain_log_renderer.py``).
The test is black-box: it constructs a StringIO-backed rich Console,
attaches a DisplayContext, calls ``emit_status_line`` directly, and
asserts on the captured output. No real I/O, no time.sleep, no
subprocess.

Each test must complete in < 0.1 s; the whole file finishes in well
under 0.5 s so the combined 60-second budget in ``make verify`` stays
unbroken.

Note on the contract:
    ``emit_status_line(unit_id, status)`` composes the canonical
    ``[status][unit_id] <sanitized status>`` line via
    ``self._build_line(timestamp, "INFO", "META", ...)`` so log
    parsers can grep on the badge contract like every other line.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay


def _make_display(width: int = 120) -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=width)
    ctx = make_display_context(console=console, env={})
    return ParallelDisplay(ctx), buf


def _make_quiet_display(width: int = 120) -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=width)
    ctx = make_display_context(console=console, env={})
    return ParallelDisplay(ctx, is_quiet=True), buf


def test_emit_status_line_emits_status_tag_with_unit_id() -> None:
    """``[status][unit_id]`` is the canonical tag for log-parser grep."""
    pd, buf = _make_display()
    pd.emit_status_line("api-endpoints", "running")
    pd.stop()
    output = buf.getvalue()
    assert "INFO" in output, f"status line missing INFO badge: {output!r}"
    assert "META" in output, f"status line missing META badge: {output!r}"
    assert "[status][api-endpoints]" in output, (
        f"status line missing [status][api-endpoints] tag: {output!r}"
    )
    assert "running" in output, f"status line missing status text: {output!r}"


def test_emit_status_line_uses_build_line_badge_contract() -> None:
    """emit_status_line goes through ``self._build_line`` for the badge contract.

    A regression that re-introduces a free ``self._console.out(...)``
    would fail this test because the badge and tag prefix would be
    missing.
    """
    pd, buf = _make_display()
    pd.emit_status_line("unit-9", "idle")
    pd.stop()
    output = buf.getvalue()
    assert "[status][unit-9]" in output, (
        f"badge-contract tag missing: {output!r}"
    )
    assert "INFO" in output, f"INFO badge missing: {output!r}"
    assert "META" in output, f"META category missing: {output!r}"


def test_emit_status_line_preserves_unit_id_verbatim() -> None:
    """Various unit_id shapes are preserved verbatim in the status line."""
    pd, buf = _make_display()
    pd.emit_status_line("reviewer-agent/2", "running")
    pd.stop()
    output = buf.getvalue()
    assert "reviewer-agent/2" in output, (
        f"unit_id 'reviewer-agent/2' must be preserved: {output!r}"
    )
    assert "[status][reviewer-agent/2]" in output, (
        f"[status][reviewer-agent/2] tag missing: {output!r}"
    )


def test_emit_status_line_quiet_mode_produces_empty_buffer() -> None:
    """``emit_status_line`` is a no-op when ``is_quiet=True`` (black-box contract).

    Quiet-mode machine-friendly runs must not surface per-unit status
    banners; this pins that contract on the observable rendered output.
    """
    pd, buf = _make_quiet_display()
    pd.emit_status_line("api-endpoints", "running")
    pd.stop()
    output = buf.getvalue()
    assert output == "", (
        f"quiet mode must produce empty output for emit_status_line; got: {output!r}"
    )


def test_emit_status_line_quiet_mode_suppresses_status_text_and_tag() -> None:
    """Quiet mode suppresses both the ``[status]`` tag and the status text body."""
    pd, buf = _make_quiet_display()
    pd.emit_status_line("unit-9", "idle")
    pd.stop()
    output = buf.getvalue()
    assert "[status]" not in output, (
        f"quiet mode must suppress the [status] tag; got: {output!r}"
    )
    assert "idle" not in output, (
        f"quiet mode must suppress the status text body; got: {output!r}"
    )
    assert "INFO" not in output, (
        f"quiet mode must suppress the INFO badge; got: {output!r}"
    )
