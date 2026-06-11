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


def _display(
    *, is_quiet: bool = False
) -> tuple[ParallelDisplay, StringIO, list[object]]:
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
    assert len(panels) == 1, (
        f"expected exactly 1 panel, got {len(panels)}: {panels!r}"
    )
    panel = panels[0]
    assert panel.title == "Next steps", f"unexpected title: {panel.title!r}"


def test_emit_info_panel_with_empty_content_still_emits() -> None:
    """An empty content string still emits a Panel gracefully."""
    pd, _, captured = _display()
    pd.emit_info_panel(title="Next steps", content="")
    pd.stop()
    panels = _panels_only(captured)
    assert len(panels) == 1, (
        f"empty content must still emit a panel, got {len(panels)}: {panels!r}"
    )


def test_emit_info_panel_emits_section_rule_in_non_compact_mode() -> None:
    """AC-05: a [info] section-rule header is emitted in non-compact mode.

    This pins the new visual-hierarchy fill from Step 2: every
    table/panel surface that previously rendered a Table or Panel
    without a section-rule header now emits ``[info]`` above the
    panel in non-compact mode. The factory uses ``width=120`` so the
    resulting mode is non-compact (compact is <60 cols).
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
    assert buf.getvalue() == "", (
        f"quiet mode must produce no output, got: {buf.getvalue()!r}"
    )
    assert captured == [], (
        f"quiet mode must not call console.print, got: {captured!r}"
    )
