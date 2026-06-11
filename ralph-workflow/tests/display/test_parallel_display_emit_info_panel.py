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


def _display() -> tuple[ParallelDisplay, StringIO, list[object]]:
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
    return ParallelDisplay(ctx), buf, captured


def test_emit_info_panel_with_title_and_content() -> None:
    """Panel renders with the requested title and content."""
    pd, _, captured = _display()
    pd.emit_info_panel(title="Next steps", content="  \u2022 Run ralph --init")
    pd.stop()
    assert len(captured) == 1, f"expected 1 panel, got {len(captured)}: {captured!r}"
    panel = captured[0]
    assert isinstance(panel, Panel), f"expected rich.panel.Panel, got {type(panel).__name__}"
    assert panel.title == "Next steps", f"unexpected title: {panel.title!r}"


def test_emit_info_panel_with_empty_content_still_emits() -> None:
    """An empty content string still emits a Panel gracefully."""
    pd, _, captured = _display()
    pd.emit_info_panel(title="Next steps", content="")
    pd.stop()
    assert len(captured) == 1, (
        f"empty content must still emit a panel, got {len(captured)}: {captured!r}"
    )
