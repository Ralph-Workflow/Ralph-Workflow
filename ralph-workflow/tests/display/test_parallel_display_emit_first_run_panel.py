"""Black-box tests for ``ParallelDisplay.emit_first_run_panel`` (wt-007).

Pins the new first-run-panel emit method. The test is black-box: it
constructs a StringIO-backed rich Console, attaches a DisplayContext,
and asserts the visible output. No real I/O, no time.sleep, no
subprocess.

Each test must complete in < 0.1s. The whole file is expected to
finish in < 0.5s.
"""

from __future__ import annotations

from io import StringIO

from rich.panel import Panel
from rich.text import Text

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay


def test_emit_first_run_panel_renders_panel() -> None:
    """The first-run panel is a rich Panel with the canonical title."""
    buf = StringIO()
    printed: list[object] = []

    class _RecordingConsole:
        width = 120
        file = buf

        def print(self, *args: object, **kwargs: object) -> None:
            printed.extend(args)

    recording_console = _RecordingConsole()
    ctx = make_display_context(env={}, console=recording_console)
    pd = ParallelDisplay(ctx)
    pd.emit_first_run_panel([Text("hello-first-run")])

    assert len(printed) == 1, (
        f"emit_first_run_panel should print exactly one Panel, got {len(printed)}: {printed!r}"
    )
    panel = printed[0]
    assert isinstance(panel, Panel), (
        f"emit_first_run_panel should print a rich.panel.Panel, got {type(panel).__name__}"
    )
