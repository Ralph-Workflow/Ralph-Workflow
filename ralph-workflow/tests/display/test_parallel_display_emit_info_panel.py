"""Black-box tests for ``ParallelDisplay.emit_info_panel`` (wt-007).

Pins the new info-panel emit method added in step 7 of the
consolidation. The test is black-box: it constructs a StringIO-backed
rich Console, attaches a DisplayContext, and asserts the visible
output. No real I/O, no time.sleep, no subprocess.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console
from rich.panel import Panel

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.theme import RALPH_THEME


def _display(*, is_quiet: bool = False) -> tuple[ParallelDisplay, StringIO, list[object]]:
    buf = StringIO()
    captured: list[object] = []
    console = Console(
        file=buf,
        force_terminal=False,
        width=120,
        color_system=None,
        theme=RALPH_THEME,
    )

    class _CaptureConsole:
        width = 120
        file = buf

        def print(self, *args: object, **kwargs: object) -> None:
            captured.extend(args)
            console.print(*args, **kwargs)

    cap_console = _CaptureConsole()
    ctx = make_display_context(console=cap_console, env={})
    return ParallelDisplay(ctx, is_quiet=is_quiet), buf, captured


def _panels_only(captured: list[object]) -> list[Panel]:
    """Filter captured renderables to only rich.panel.Panel instances.

    The emit_info_panel call now also emits a ``[info]`` section-rule
    header before the panel; only the Panel is the unit under test for
    the title/content assertions.
    """
    return [item for item in captured if isinstance(item, Panel)]


def test_emit_info_panel_with_title_and_content() -> None:
    """Panel renders with the requested title and content."""
    pd, _, captured = _display()
    pd.emit_info_panel(title="Next steps", content="  \u2022 Run ralph --init")
    pd.stop()
    panels = _panels_only(captured)
    assert len(panels) == 1, f"expected exactly 1 panel, got {len(panels)}: {panels!r}"
    panel = panels[0]
    assert panel.title == "Next steps", f"unexpected title: {panel.title!r}"


def test_emit_info_panel_with_empty_content_still_emits() -> None:
    """An empty content string still emits a Panel gracefully."""
    pd, _, captured = _display()
    pd.emit_info_panel(title="Next steps", content="")
    pd.stop()
    panels = _panels_only(captured)
    assert len(panels) == 1, f"empty content must still emit a panel, got {len(panels)}: {panels!r}"


def test_emit_info_panel_emits_section_rule_header() -> None:
    """AC-05: a [info] section-rule header is emitted above the panel.

    This pins the visual-hierarchy fill: every table/panel surface
    that previously rendered a Table or Panel without a section-rule
    header now emits ``[info]`` above the panel. The factory uses
    ``width=120`` so the panel is rendered in the single default mode.
    """
    pd, buf, _ = _display()
    pd.emit_info_panel(title="Next steps", content="  \u2022 Run ralph --init")
    pd.stop()
    output = buf.getvalue()
    assert "[info]" in output, f"expected [info] section rule in output: {output!r}"


def test_emit_info_panel_quiet_mode_emits_nothing() -> None:
    """AC-05: the quiet-mode no-output contract for emit_info_panel.

    The pinned contract: when DisplayContext.is_quiet=True, the emit
    method must short-circuit before any rendering happens. This
    closes the missing quiet-mode coverage for emit_info_panel.
    """
    pd, buf, captured = _display(is_quiet=True)
    pd.emit_info_panel(title="Next steps", content="  \u2022 Run ralph --init")
    pd.stop()
    assert buf.getvalue() == "", f"quiet mode must produce no output, got: {buf.getvalue()!r}"
    assert captured == [], f"quiet mode must not call console.print, got: {captured!r}"
# ---------------------------------------------------------------------------
# DA-001 (S-5 / AC-04): the info Panel / unboxed body honor
# ``DisplayContext.body_measure()`` so prose on a very wide console
# (e.g. 250 cols) does not run the full terminal width.
# ---------------------------------------------------------------------------


def test_emit_info_panel_caps_body_width_at_body_measure_on_wide_console() -> None:
    """DA-001: 250-col console -> Panel width == body_measure (NOT 250)."""
    buf = StringIO()
    console = Console(
        file=buf,
        force_terminal=False,
        width=250,
        color_system=None,
        theme=RALPH_THEME,
    )
    ctx = make_display_context(console=console, env={})
    assert ctx.width == 250
    body_measure = ctx.body_measure()

    captured_panel: list[Panel] = []

    class _CaptureConsole:
        width = 250
        file = buf

        def print(self, *args: object, **kwargs: object) -> None:
            captured_panel.extend(arg for arg in args if isinstance(arg, Panel))
            console.print(*args, **kwargs)

    cap_ctx = make_display_context(console=_CaptureConsole(), env={})
    pd_cap = ParallelDisplay(cap_ctx)
    pd_cap.emit_info_panel(title="Next steps", content="Some short body content.")
    pd_cap.stop()
    assert len(captured_panel) == 1, (
        f"expected exactly 1 panel under wide console, got {len(captured_panel)}"
    )
    panel = captured_panel[0]
    # Pre-fix bug: no width was passed to Panel, so Rich defaulted
    # to the console's 250 cols and prose ran full terminal width.
    # DA-001 pins the panel.width to body_measure() instead.
    assert panel.width == body_measure, (
        f"info Panel width must equal body_measure ({body_measure}) on a "
        f"250-col console, got {panel.width!r}"
    )
    assert panel.width < 250, (
        f"info Panel must not run the full terminal width; got {panel.width!r}"
    )


def test_emit_info_panel_unboxed_body_caps_width_on_wide_height_constrained_console() -> None:
    """DA-001: 12-row / 250-col console -> unboxed body line length stays <= body_measure.

    The height-constrained path emits a heading + body pair (no
    border). The body print must still honor ``body_measure()`` so
    a 250-col terminal does not print 250-char prose lines from an
    info block.
    """
    buf = StringIO()
    console = Console(
        file=buf,
        force_terminal=False,
        width=250,
        color_system=None,
        theme=RALPH_THEME,
    )
    ctx = make_display_context(console=console, env={}, force_height=12)
    assert ctx.is_height_constrained()
    pd = ParallelDisplay(ctx)
    pd.emit_info_panel(title="Next steps", content="Some short body content.")
    pd.stop()
    rendered = buf.getvalue()
    # The body line is everything after the title line and before
    # any trailing whitespace; assert the longest non-rule line
    # stays at or below body_measure.
    body_lines = [
        line
        for line in rendered.splitlines()
        if line
        and "Next steps" not in line
        and "\u2500" not in line
    ]
    assert body_lines, f"expected a body line in rendered output, got: {rendered!r}"
    longest = max(len(line) for line in body_lines)
    assert longest <= ctx.body_measure() + 4, (
        f"height-constrained body line must respect body_measure ({ctx.body_measure()}); "
        f"longest line was {longest!r}: {rendered!r}"
    )
