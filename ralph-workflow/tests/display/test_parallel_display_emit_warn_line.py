"""Black-box tests for ``ParallelDisplay.emit_warn_line`` (wt-028-display).

Pins the public emit method that consolidates per-unit warn lines
onto ``ParallelDisplay`` (closing the free-function ``_console.print``
bypass in the warn-line helpers). The test is black-box: it
constructs a StringIO-backed rich Console, attaches a DisplayContext,
calls ``emit_warn_line`` directly, and asserts on the captured
output. No real I/O, no time.sleep, no subprocess.

Each test must complete in < 0.1 s; the whole file finishes in well
under 0.5 s so the combined 60-second budget in ``make verify`` stays
unbroken.

Note on the contract:
    ``emit_warn_line(unit_id, tag, message)`` composes a ``WARN``
    level badge line using ``[tag][unit_id] <message>``. The
    category is ``TAG_CATEGORY.get(tag, "META")`` so unknown tags
    fall back to ``META``. Tags like ``error`` map to ``CONT``.
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


def test_emit_warn_line_emits_warn_level_meta_category() -> None:
    """Default ``META`` category is used when the tag is not in TAG_CATEGORY."""
    pd, buf = _make_display()
    pd.emit_warn_line("unit-1", "warn", "tail-latency rose to 800ms")
    pd.stop()
    output = buf.getvalue()
    assert "WARN" in output, f"warn line missing WARN level: {output!r}"
    assert "META" in output, f"warn line missing META category: {output!r}"
    assert "[warn][unit-1]" in output, (
        f"warn line missing [warn][unit-1] tag: {output!r}"
    )
    assert "tail-latency rose to 800ms" in output, (
        f"warn line missing message: {output!r}"
    )


def test_emit_warn_line_uses_meta_category_for_unknown_tag() -> None:
    """Tags outside ``TAG_CATEGORY`` fall back to the ``META`` category."""
    pd, buf = _make_display()
    pd.emit_warn_line("unit-1", "totally-unknown-tag", "hello")
    pd.stop()
    output = buf.getvalue()
    assert "WARN" in output, f"WARN level missing: {output!r}"
    assert "META" in output, (
        f"unknown tag must default to META category: {output!r}"
    )
    assert "CONT" not in output, (
        f"unknown tag must NOT pick CONT category: {output!r}"
    )


def test_emit_warn_line_preserves_unit_id_verbatim() -> None:
    """Common unit_id shapes survive verbatim into the rendered output."""
    pd, buf = _make_display()
    pd.emit_warn_line("reviewer-agent/3", "failure", "amend needed")
    pd.stop()
    output = buf.getvalue()
    assert "reviewer-agent/3" in output, (
        f"unit_id 'reviewer-agent/3' must be preserved: {output!r}"
    )
    assert "[failure][reviewer-agent/3]" in output, (
        f"[failure][reviewer-agent/3] tag missing: {output!r}"
    )


def test_emit_warn_line_renders_message_verbatim() -> None:
    """The message text is rendered into the captured output as the body."""
    pd, buf = _make_display()
    pd.emit_warn_line("unit-1", "warn", "the system is on fire")
    pd.stop()
    output = buf.getvalue()
    assert "the system is on fire" in output, (
        f"warn line missing message body: {output!r}"
    )
