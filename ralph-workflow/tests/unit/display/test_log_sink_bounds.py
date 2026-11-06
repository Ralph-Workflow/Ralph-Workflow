"""The terminal log boundary renders every record as bounded, separate lines.

Every loguru record that reaches the terminal passes through the sinks in
``ralph.display.log_sink``.  Callers interpolate exception text and agent
output into messages, so the boundary is the one place that can guarantee a
record never becomes a wall of text, and that consecutive records never run
together on one line.  Full text stays available in the file sinks.
"""

from __future__ import annotations

import io

from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.log_sink import make_sanitizing_log_sink, make_stderr_log_sink
from ralph.display.theme import RALPH_THEME


def test_stderr_sink_keeps_consecutive_records_on_separate_lines() -> None:
    written: list[str] = []
    sink = make_stderr_log_sink(writer=written.append)

    sink("first record\n")
    sink("second record\n")

    assert "".join(written).splitlines() == ["first record", "second record"]


def test_stderr_sink_bounds_one_enormous_line() -> None:
    written: list[str] = []
    sink = make_stderr_log_sink(writer=written.append)

    sink("prefix - " + "x" * 50_000 + "\n")

    out = "".join(written)
    assert out.startswith("prefix - ")
    assert len(out) < 2_000
    assert "omitted" in out


def test_stderr_sink_bounds_a_record_with_hundreds_of_lines() -> None:
    written: list[str] = []
    sink = make_stderr_log_sink(writer=written.append)

    sink("header\n" + "\n".join(f"line {i}" for i in range(500)) + "\n")

    lines = "".join(written).splitlines()
    assert lines[0] == "header"
    assert len(lines) < 60
    assert any("more lines omitted" in line for line in lines)


def test_stderr_sink_leaves_an_ordinary_multiline_record_untouched() -> None:
    written: list[str] = []
    sink = make_stderr_log_sink(writer=written.append)

    sink("failed:\n  reason one\n  reason two\n")

    assert "".join(written) == "failed:\n  reason one\n  reason two\n"


def test_console_sink_bounds_one_enormous_line() -> None:
    stream = io.StringIO()
    sink = make_sanitizing_log_sink(
        make_display_context(
            console=Console(file=stream, force_terminal=False, width=100, theme=RALPH_THEME),
            env={},
        )
    )

    sink("prefix - " + "y" * 50_000 + "\n")

    assert "prefix - " in stream.getvalue()
    assert stream.getvalue().count("y") < 2_000
    assert "omitted" in stream.getvalue()
