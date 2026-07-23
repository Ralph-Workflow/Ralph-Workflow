"""Black-box containment tests for terminal-escape leakage at display sinks.

The bug is that interactive agents (Claude Code, etc.) emit raw VT
sequences into Ralph's display path. The five sinks that carry
agent-origin text MUST strip every relevant escape class so an
adversarial TUI repaint (``ESC[?1049h`` alternate screen,
``ESC[2J`` erase display, ``ESC[>0c`` private-parameter CSI) cannot
blank Ralph's screen or overwrite existing log lines.

This file drives every sink with the hostile line
``"\\x1b[?1049h\\x1b[2J\\x1b[>0cboom"`` (visible text ``boom``) and
asserts:

  - the captured output contains no bare ``\\x1b`` byte, and
  - no body residue from the captured input (``[?1049h``, ``[2J``,
    ``[>0c``) survives, and
  - the visible ``boom`` is preserved.

Tests use a rich ``Console(file=io.StringIO())`` -- never the real
stdout -- and an injectable ``DisplayContext`` so the assertion is
deterministic.

No real subprocess, no time.sleep, no wall-clock waits (audit_test_policy
forbids them).
"""

from __future__ import annotations

import io

from rich.console import Console

from ralph.display._plain_constants import _sanitize
from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.activity_model import render_event_line
from ralph.display.context import DisplayContext, make_display_context
from ralph.display.parallel_display import (
    ParallelDisplay,
    emit_activity_line,
    strip_markup,
)

# Hostile line combining the three real failure classes -- alternate screen,
# erase display, and the private-parameter CSI form the previous regex missed.
HOSTILE_LINE = "\x1b[?1049h\x1b[2J\x1b[>0cboom"

# Bodies left behind by previous incomplete regexes -- if any of these
# appears in captured output, the sink is still leaking.
_FORBIDDEN_BODIES = ("[?1049h", "[2J", "[>0c")


def _make_parallel_display(
    *, is_quiet: bool = False
) -> tuple[ParallelDisplay, DisplayContext, io.StringIO]:
    buf = io.StringIO()
    console = Console(
        file=buf,
        force_terminal=False,
        color_system=None,
        width=120,
    )
    ctx = make_display_context(console=console, env={"CI": "1"})
    return ParallelDisplay(ctx, is_quiet=is_quiet), ctx, buf


def _assert_no_escape_leak(output: str, *, sink_label: str) -> None:
    """Assert the captured output has no ``\\x1b`` byte and no body residue.

    ``sink_label`` identifies the failing sink so a regression points
    the operator at the right place.
    """
    assert "\x1b" not in output, (
        f"{sink_label}: bare ESC byte leaked into display output: {output!r}"
    )
    for forbidden in _FORBIDDEN_BODIES:
        assert forbidden not in output, (
            f"{sink_label}: hostile body {forbidden!r} leaked through sink; "
            f"output={output!r}"
        )


# ---------------------------------------------------------------------------
# Sink 1: ralph.display._plain_constants._sanitize
# ---------------------------------------------------------------------------


def test_sanitize_plain_constants_strips_hostile_line() -> None:
    """``_sanitize`` is the leaf helper that backs nearly every display sink.

    The OLD regex ``[0-9;]*m`` cannot match ``ESC[?1049h``, ``ESC[2J``,
    ``ESC[>0c`` (the '?', 'J', '>' are outside the [0-9;] character class),
    so all three leaked. The replacement uses
    ``strip_terminal_control`` which covers the full parameter-byte range.
    """
    sanitized = _sanitize(HOSTILE_LINE)
    assert sanitized == "boom", (
        f"_sanitize must strip the hostile prefixes and keep 'boom'; "
        f"got {sanitized!r}"
    )
    _assert_no_escape_leak(sanitized, sink_label="_sanitize")


def test_sanitize_plain_constants_preserves_literal_brackets() -> None:
    """``_sanitize`` strips terminal control sequences but PRESERVES literal Rich markup.

    After the wt-028-display consolidation, every ``_sanitize`` consumer
    in :mod:`ralph.display.parallel_display` prints the result through
    a Console with ``markup=False`` -- Rich therefore does NOT
    interpret ``[green]ok[/green]`` as a style sequence, and stripping
    it here would mutate literal agent content (``[result] ok`` ->
    ``ok``). The function therefore keeps only the terminal-control
    strip; literal ``[bracket]`` content surfaces verbatim to the
    operator.
    """
    sanitized = _sanitize("[green]ok[/green]")
    assert sanitized == "[green]ok[/green]"


# ---------------------------------------------------------------------------
# Sink 2: ralph.display.parallel_display.strip_markup (public helper)
# ---------------------------------------------------------------------------


def test_parallel_display_strip_markup_strips_hostile_line() -> None:
    """The public ``ParallelDisplay.strip_markup`` helper used by subscribers.

    Its OLD form imported and reused the SGR-only ``_ANSI_ESCAPE`` constant
    from ``_plain_constants``. Step 5(a) rewrites it to delegate to
    ``strip_terminal_control`` after this test would have caught a leak.
    """
    stripped = strip_markup(HOSTILE_LINE)
    assert stripped == "boom", (
        f"strip_markup must strip the hostile prefixes and keep 'boom'; "
        f"got {stripped!r}"
    )
    _assert_no_escape_leak(stripped, sink_label="strip_markup")


def test_parallel_display_strip_markup_preserves_literal_brackets() -> None:
    """``strip_markup`` strips terminal control sequences only.

    After the wt-028-display consolidation the helper no longer strips
    Rich markup -- every consumer prints through ``markup=False`` so a
    literal ``[green]ok[/green]`` cannot reach the terminal as
    markup. The function therefore keeps the literal brackets
    verbatim; only the terminal control sequences are removed.
    """
    assert strip_markup("[green]ok[/green]") == "[green]ok[/green]"
    assert strip_markup("plain text") == "plain text"


# ---------------------------------------------------------------------------
# Sink 3: parallel_display's module-level emit_activity_line
# ---------------------------------------------------------------------------


def test_module_emit_activity_line_strips_hostile_when_unit_id_none() -> None:
    """The module-level emit_activity_line print-when-display-is-None branch.

    Reached from ralph/pipeline/activity_stream.py:344. The OLD code at
    parallel_display.py:3080/3082 used ``console.print(line)`` with no
    sanitization, so the raw hostile line reached the user's terminal.
    """
    _pd, ctx, buf = _make_parallel_display()
    emit_activity_line(display=None, unit_id=None, line=HOSTILE_LINE, display_context=ctx)
    output = buf.getvalue()
    assert "boom" in output, (
        f"the visible 'boom' must survive sanitization; got {output!r}"
    )
    _assert_no_escape_leak(output, sink_label="module emit_activity_line (unit_id=None)")


def test_module_emit_activity_line_strips_hostile_when_unit_id_set() -> None:
    """The second module-level branch (unit_id set) is just as exposed."""
    _pd, ctx, buf = _make_parallel_display()
    emit_activity_line(display=None, unit_id="u7", line=HOSTILE_LINE, display_context=ctx)
    output = buf.getvalue()
    assert "boom" in output, (
        f"the visible 'boom' must survive sanitization; got {output!r}"
    )
    _assert_no_escape_leak(
        output, sink_label="module emit_activity_line (unit_id=u7)"
    )


# ---------------------------------------------------------------------------
# Sink 4: ParallelDisplay.emit (the standard instance method path)
# ---------------------------------------------------------------------------


def test_parallel_display_emit_strips_hostile_line() -> None:
    """ParallelDisplay.emit -> emit_log_line -> emit_activity_line path."""
    pd, _ctx, buf = _make_parallel_display()
    pd.emit(unit_id="u1", line=HOSTILE_LINE)
    pd.stop()
    output = buf.getvalue()
    assert "boom" in output, (
        f"the visible 'boom' must survive sanitization; got {output!r}"
    )
    _assert_no_escape_leak(output, sink_label="ParallelDisplay.emit")


# ---------------------------------------------------------------------------
# Sink 5: ParallelDisplay._render_titled_lines
# ---------------------------------------------------------------------------


def test_render_titled_lines_strips_hostile_line() -> None:
    """The artifact / markdown-handoff body sink used by PLAN/FIX/ANALYSIS.

    The OLD code at parallel_display.py:2503-2511 printed ``line`` with
    ``markup=False`` but no escape stripping, so a hostile agent line in a
    handoff body would land in the user's terminal. Body lines here use
    ``markup=False`` (literal brackets), so the test must use ``strip_terminal_control``
    not ``_sanitize``.
    """
    pd, _ctx, buf = _make_parallel_display()
    pd._render_titled_lines(
        title="t",
        style_phase="execution",
        lines=[HOSTILE_LINE],
    )
    output = buf.getvalue()
    assert "boom" in output, (
        f"the visible 'boom' must survive sanitization; got {output!r}"
    )
    _assert_no_escape_leak(output, sink_label="_render_titled_lines")


# ---------------------------------------------------------------------------
# Sink 6: activity_model.render_event_line (the activity_router path)
# ---------------------------------------------------------------------------


def test_render_event_line_strips_hostile_line() -> None:
    """The activity_router render path that previously only markup-escaped.

    Line 95 of activity_model.py used ``rich.markup.escape`` which
    neutralises rich MARKUP only -- it does NOT strip ANSI/C0 bytes. The
    hostile line flows straight into this sink from
    activity_router.py:197/:214. After step 5(e) it delegates to
    ``strip_terminal_control`` before truncation+escape.
    """
    rendered = render_event_line(
        kind=ActivityEventKind.TEXT,
        content=HOSTILE_LINE,
        timestamp="2026-01-01T00:00:00+00:00",
    )
    _assert_no_escape_leak(rendered, sink_label="render_event_line")
    assert "boom" in rendered, (
        f"the visible 'boom' must survive; got {rendered!r}"
    )


def test_render_event_line_preserves_literal_brackets() -> None:
    """Plain-text path keeps literal Rich markup unchanged (AC-05 / AC-09).

    After the wt-028-display consolidation, :func:`render_event_line`
    delegates to :func:`ralph.display.agent_event_renderer.render_event`
    with ``escape_body=False`` because plain-text consumers
    (``ParallelDisplay`` with ``markup=False``) print the returned
    string verbatim. Escaping literal ``[red]`` content into
    ``\\[red]`` mutates agent output (analysis-feedback contract). The
    rich-Text path's escape contract is enforced separately via
    :func:`test_content_escape_strips_rich_markup` in
    :mod:`tests.display.test_agent_event_renderer`.
    """
    rendered = render_event_line(
        kind=ActivityEventKind.TEXT,
        content="[red]injected[/red]",
        timestamp="2026-01-01T00:00:00+00:00",
    )
    assert "[red]injected[/red]" in rendered
    assert "\\[red]" not in rendered
