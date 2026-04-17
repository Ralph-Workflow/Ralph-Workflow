from __future__ import annotations

import re
from io import StringIO

from rich.console import Console

from ralph.display.parallel_display import ParallelDisplay
from ralph.pipeline.worker_state import WorkerStatus

ANSI_ESCAPE_RE = re.compile(r"\x1b\[")


def _make_display(
    *,
    force_terminal: bool,
    columns: int | None = None,
) -> tuple[ParallelDisplay, StringIO]:
    buffer = StringIO()
    if columns is None:
        console = Console(file=buffer, force_terminal=force_terminal, highlight=False)
    else:
        console = Console(
            file=buffer,
            force_terminal=force_terminal,
            highlight=False,
            width=columns,
        )
    return ParallelDisplay(console=console, env={}), buffer


def _lines(buffer: StringIO) -> list[str]:
    return [line for line in buffer.getvalue().splitlines() if line]


def test_non_tty_no_ansi() -> None:
    display, buffer = _make_display(force_terminal=False)

    display.emit("unit-1", "some output line")

    output = buffer.getvalue()
    assert "[unit-1] some output line" in output
    assert ANSI_ESCAPE_RE.search(output) is None


def test_narrow_no_crash(monkeypatch) -> None:
    monkeypatch.setenv("COLUMNS", "40")
    display, buffer = _make_display(force_terminal=True, columns=40)

    display.emit("unit-1", "first line")
    display.emit("unit-1", "second line")
    display.set_status("unit-1", WorkerStatus.RUNNING)

    output = buffer.getvalue()
    assert output
    assert "[unit-1]" in output


def test_prefixed_lines_format() -> None:
    display, buffer = _make_display(force_terminal=False)

    display.emit("unit-1", "alpha")
    display.emit("unit-2", "beta")
    display.emit("unit-1", "gamma")

    lines = _lines(buffer)
    assert lines == [
        "[unit-1] alpha",
        "[unit-2] beta",
        "[unit-1] gamma",
    ]
