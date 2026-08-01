"""Black-box tests for ``ParallelDisplay.emit_log_line`` (wt-028-display).

Pins the public emit method that consolidates raw per-unit log
output onto ``ParallelDisplay`` (closing a free-function bypass in
``ralph/display/plain_renderer/_plain_log_renderer.py``). The test is
black-box: it constructs a StringIO-backed rich Console, attaches a
DisplayContext, calls ``emit_log_line`` directly, and asserts on the
captured output. No real I/O, no time.sleep, no subprocess.

Each test must complete in < 0.1 s; the whole file finishes in well
under 0.5 s so the combined 60-second budget in ``make verify`` stays
unbroken.

Note on the contract:
    ``emit_log_line(unit_id, line)`` is a thin wrapper around
    ``emit_activity_line(unit_id, "raw", line)``. The "raw" kind
    maps to the ``content`` tag (``INFO`` level, ``CONT`` category
    per ``_KIND_TO_TAG`` / ``TAG_CATEGORY``).
"""

from __future__ import annotations

from io import StringIO

from rich.cells import cell_len
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


def test_emit_log_line_emits_info_badge_with_unit_id() -> None:
    """The standard INFO badge and unit_id appear in the captured output."""
    pd, buf = _make_display()
    pd.emit_log_line("unit-7", "hello world")
    pd.stop()
    output = buf.getvalue()
    assert "[unit-7]" in output, f"log line missing unit_id marker: {output!r}"
    assert "hello world" in output, f"log line missing content: {output!r}"


def test_emit_log_line_routes_via_activity_line() -> None:
    """emit_log_line is a thin wrapper around emit_activity_line(kind='raw')."""
    pd, buf = _make_display()
    pd.emit_log_line("unit-x", "RAW_PAYLOAD_TOKEN")
    pd.stop()
    output = buf.getvalue()
    assert "RAW_PAYLOAD_TOKEN" in output, f"raw payload missing from output: {output!r}"
    assert "[output]" in output, f"raw kind tag [output] missing from output: {output!r}"
    assert "[unit-x]" in output, f"unit_id marker missing: {output!r}"


def test_emit_log_line_regression_wrapped_rows_retain_grep_carriers() -> None:
    """S-5: every wrapped row remains independently greppable by category and unit."""
    pd, buf = _make_display(width=40)
    pd.emit_log_line("unit-wide", "alpha bravo charlie delta echo foxtrot golf hotel")
    pd.stop()
    output_lines = [line for line in buf.getvalue().splitlines() if line]
    assert len(output_lines) > 1
    assert all("[output][unit-wide]" in line for line in output_lines)
    assert all(line[0:2].isdigit() and line[2] == ":" for line in output_lines)


def test_emit_activity_line_regression_wrapped_structured_rows_retain_grep_carriers() -> None:
    """S-5: structured continuations retain their event and unit carriers."""
    pd, buf = _make_display(width=40)
    pd.emit_activity_line("unit-wide", "error", "alpha bravo charlie delta echo foxtrot")
    pd.stop()
    output_lines = [line for line in buf.getvalue().splitlines() if line]
    assert len(output_lines) > 1
    assert all("[error][unit-wide]" in line for line in output_lines)
    assert all(line[0:2].isdigit() and line[2] == ":" for line in output_lines)


def test_emit_log_line_regression_folds_unbroken_content_to_the_supported_width() -> None:
    """S-5: an overlong path-like token folds without clipping its recovery tail."""
    pd, buf = _make_display(width=40)
    token = "/workspace/" + "x" * 60 + "/important-tail.py"
    pd.emit_log_line("unit-wide", token)
    pd.stop()
    output_lines = [line for line in buf.getvalue().splitlines() if line]
    assert len(output_lines) > 1
    assert all(cell_len(line) <= 40 for line in output_lines)
    body_chunks = [line.partition("[unit-wide] ")[2] for line in output_lines]
    assert "".join(body_chunks) == token
    assert body_chunks[-1].endswith("y")


def test_emit_log_line_preserves_unit_id_verbatim() -> None:
    """Various unit_id shapes are preserved without rewriting characters."""
    pd, buf = _make_display()
    pd.emit_log_line("api-endpoints/v2", "preserved-id-check")
    pd.stop()
    output = buf.getvalue()
    assert "api-endpoints/v2" in output, (
        f"unit_id 'api-endpoints/v2' must be preserved verbatim: {output!r}"
    )


def test_emit_log_line_sanitizes_ansi_sequences() -> None:
    """ANSI escape sequences in the line content are stripped before render."""
    pd, buf = _make_display()
    pd.emit_log_line("unit-7", "before\x1b[31mCOLORED\x1b[0mafter")
    pd.stop()
    output = buf.getvalue()
    assert "\x1b[31m" not in output, (
        f"ANSI escape must be stripped from rendered output: {output!r}"
    )
    assert "before" in output and "after" in output, (
        f"surrounding text must survive sanitization: {output!r}"
    )


def test_emit_log_line_strips_newlines_from_unit_id() -> None:
    """Embedded newlines in unit_id are stripped so the line layout stays intact."""
    pd, buf = _make_display()
    pd.emit_log_line("unit\nBREAK", "hello world")
    pd.stop()
    output = buf.getvalue()
    content_lines = output.rstrip("\n").split("\n")
    assert len(content_lines) == 1, (
        f"unit_id newline must not split the rendered line; got {content_lines!r}"
    )
    assert "[unit BREAK]" in content_lines[0], (
        f"sanitized unit_id must replace newline with space: {content_lines!r}"
    )


def test_emit_log_line_quiet_mode_produces_empty_buffer() -> None:
    """``emit_log_line`` is a no-op when ``is_quiet=True`` (black-box contract).

    Quiet-mode machine-friendly runs must not surface per-unit raw-log
    lines; this pins that contract on the observable rendered output.
    """
    pd, buf = _make_quiet_display()
    pd.emit_log_line("unit-7", "should-not-appear")
    pd.stop()
    output = buf.getvalue()
    assert output == "", f"quiet mode must produce empty output for emit_log_line; got: {output!r}"


def test_emit_log_line_quiet_mode_still_emits_nothing_for_raw_payload() -> None:
    """Quiet mode suppresses ``emit_log_line`` even for non-trivial payloads."""
    pd, buf = _make_quiet_display()
    pd.emit_log_line("unit-x", "RAW_PAYLOAD_TOKEN")
    pd.stop()
    output = buf.getvalue()
    assert "RAW_PAYLOAD_TOKEN" not in output, (
        f"quiet mode must suppress raw payload; got: {output!r}"
    )
    assert "[output]" not in output, f"quiet mode must suppress the [output] tag; got: {output!r}"
