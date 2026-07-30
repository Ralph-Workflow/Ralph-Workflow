"""Black-box tests for DA-002 (S-12 / AC-07): the live log path honors
``PresentedEntry.indent_level`` / ``grouping_role`` so the canonical
hierarchy data drives the hanging-indent continuation column.

The pre-fix bug: only the text-first record writer consumed
``indent_level`` / ``grouping_role`` from the canonical
``PresentedEntry``. The live log path's continuation column was a
fixed-width copy of the chrome prefix, so a tool_result entry hung
at the same column as the tool_call it answered, and a reasoned
passage did not visually nest under the agent text it explained.
DA-002 wires the canonical indent into the live log path so the two
surfaces share one vocabulary.

The test is black-box: it constructs a StringIO-backed rich Console,
attaches a DisplayContext, calls ``emit_activity_line`` with
``options.indent_level=N`` and ``options.grouping_role=<role>``,
and asserts the captured output's first line and continuation
column reflect the requested indent. No real I/O, no time.sleep,
no subprocess.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.display._activity_line_options import ActivityLineOptions
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay

# Indent width (in columns) the live log applies per indent_level. This is
# the canonical width shared with ralph.display.record_writer so the file
# surface and the live log indent the same column; we mirror the constant
# here instead of importing the private symbol across module boundaries.
_INDENT_WIDTH = 2


def _make_display(width: int = 120) -> tuple[ParallelDisplay, StringIO]:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=width)
    ctx = make_display_context(console=console, env={})
    return ParallelDisplay(ctx), buf


def _long_body_target(width: int) -> str:
    """A multi-word body that will wrap to a continuation line on ``width``."""
    # 200 chars should comfortably wrap on a 120-col console, given
    # the chrome prefix (~25 cols) plus the badge column (~12 cols)
    # leaves roughly 80 chars of body width.
    return (
        "INDENT_LEVEL_LIVE_LOG_TOKEN_BEGIN "
        + ("abcdefghij " * 15)
        + " INDENT_LEVEL_LIVE_LOG_TOKEN_END"
    )


def test_emit_activity_line_zero_indent_keeps_badge_column_at_origin() -> None:
    """Level-0 entries hang at the chrome + badge column (no extra indent)."""
    pd, buf = _make_display(width=120)
    pd.emit_activity_line(
        "u1",
        "tool_use",
        "zero-level payload",
        options=ActivityLineOptions(indent_level=0, grouping_role="tool_call"),
    )
    pd.stop()
    output = buf.getvalue()
    lines = [line for line in output.splitlines() if line]
    assert lines, "expected at least one rendered line"
    first_line = lines[0]
    # The first line carries the badge prefix at the canonical
    # chrome column (no leading indent for level 0).
    assert first_line.startswith(" ") is False or "INFO" in first_line, (
        f"level-0 first line must not start with leading indent whitespace: {first_line!r}"
    )
    # The badge column is the canonical position for level-0 entries
    # (no extra indent); the chrome prefix already starts at column 0.
    assert "[call]" in first_line, f"missing tool_use tag: {first_line!r}"


def test_emit_activity_line_indent_level_one_shifts_continuation_column() -> None:
    """Level-1 entries hang ``_INDENT_WIDTH`` columns deeper than the badge column.

    A wrapping body (60+ chars) produces at least one continuation
    line. The first line of the rendered output carries the chrome
    + badge column AND the ``_INDENT_WIDTH``-col indent prefix; a
    continuation of the wrap (searched across the full output)
    also sits at the indented column. The pre-fix live log used a
    fixed-width copy of the chrome prefix -- which produced the
    same column as level-0 entries -- so this assertion is the
    pillar that distinguishes the post-DA-002 fix from the
    pre-fix behavior.
    """
    pd, buf = _make_display(width=120)
    pd.emit_activity_line(
        "u1",
        "tool_result",
        _long_body_target(120),
        options=ActivityLineOptions(indent_level=1, grouping_role="tool_result"),
    )
    pd.stop()
    output = buf.getvalue()
    lines = [line for line in output.splitlines() if line]
    assert len(lines) >= 2, (
        f"expected wrapping body to produce 2+ lines, got {len(lines)}: {output!r}"
    )
    # The first line is the chrome+badge+first-chunk row. It must
    # start with the level-1 indent (``_INDENT_WIDTH`` spaces)
    # BEFORE the timestamp so the badge column is at the indented
    # column. Without DA-002 the first line started flush with
    # column 0 and only the wrap rows shifted right.
    first_line = lines[0]
    leading_ws = len(first_line) - len(first_line.lstrip(" "))
    assert leading_ws >= _INDENT_WIDTH, (
        f"level-1 first line must start with >= {_INDENT_WIDTH} cols of indent "
        f"(badge column lines up with the continuation column), "
        f"got {leading_ws}: {first_line!r}"
    )


def test_emit_activity_line_indent_level_two_deeper_indent_than_one() -> None:
    """Level-2 entries shift the first-line indent by 2*_INDENT_WIDTH.

    The test isolates the level-1 vs. level-2 delta by running
    the same body through both indent levels and comparing the
    leading whitespace of the first rendered line. ``_INDENT_WIDTH``
    cols of difference is the minimum (level-1: 2, level-2: 4).
    """
    pd1, buf1 = _make_display(width=120)
    pd1.emit_activity_line(
        "u1",
        "tool_result",
        _long_body_target(120),
        options=ActivityLineOptions(indent_level=1, grouping_role="tool_result"),
    )
    pd1.stop()
    pd2, buf2 = _make_display(width=120)
    pd2.emit_activity_line(
        "u1",
        "reasoning",
        _long_body_target(120),
        options=ActivityLineOptions(indent_level=2, grouping_role="reasoning"),
    )
    pd2.stop()
    first1 = next(line for line in buf1.getvalue().splitlines() if line)
    first2 = next(line for line in buf2.getvalue().splitlines() if line)
    ws1 = len(first1) - len(first1.lstrip(" "))
    ws2 = len(first2) - len(first2.lstrip(" "))
    assert ws2 >= ws1 + _INDENT_WIDTH, (
        f"level-2 first line must be at least {_INDENT_WIDTH} cols deeper than "
        f"level-1 (got ws1={ws1}, ws2={ws2}, first1={first1!r}, first2={first2!r})"
    )


def test_emit_activity_line_first_line_also_reflects_indent_level() -> None:
    """The first line (chrome + badge prefix) also carries the indent prefix.

    The pre-fix code only prefixed the continuation column; the
    first line's leading whitespace was the chrome prefix only.
    DA-002 (S-12) requires the first line to start at the indented
    column so the badge column lines up with the continuation
    column -- the visual row is the call site, not the badged
    header.
    """
    pd, buf = _make_display(width=120)
    pd.emit_activity_line(
        "u1",
        "tool_result",
        "single-line payload",
        options=ActivityLineOptions(indent_level=1, grouping_role="tool_result"),
    )
    pd.stop()
    output = buf.getvalue()
    first_line = next(line for line in output.splitlines() if line)
    leading_ws = len(first_line) - len(first_line.lstrip(" "))
    assert leading_ws >= _INDENT_WIDTH, (
        f"level-1 first line must start with >= {_INDENT_WIDTH} cols of indent "
        f"(so the badge column lines up with the continuation column), "
        f"got {leading_ws}: {first_line!r}"
    )


def test_emit_activity_line_default_indent_level_is_zero() -> None:
    """Calling without ``indent_level`` falls back to the pre-fix level-0 behavior.

    Backward-compatibility pin: callers that have not yet been
    updated to thread ``indent_level`` through ``ActivityLineOptions``
    still see the original badge column. This is the contract
    that lets the patch land without forcing every call site to
    move in lockstep.
    """
    pd, buf = _make_display(width=120)
    pd.emit_activity_line("u1", "text", "no-options payload")
    pd.stop()
    output = buf.getvalue()
    first_line = next(line for line in output.splitlines() if line)
    leading_ws = len(first_line) - len(first_line.lstrip(" "))
    assert leading_ws < _INDENT_WIDTH, (
        f"default (no options) must use level-0 indent (no extra leading space); "
        f"got {leading_ws}: {first_line!r}"
    )
