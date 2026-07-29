"""Unit tests for the AgyParser.

The v1.1.8 source record retains the earlier plain-text ``--print`` wire
observations as historical parser evidence. Its current paid stream-json probe
is explicitly not run, so these tests preserve Ralph's existing plain-text
parser contract without claiming it describes unmeasured v1.1.8 stream output.

The AgyParser classifies plain-text output as ``type='text'`` (not ``'raw'``)
so the smoke report renders model content as text.
"""

from __future__ import annotations

from ralph.agents.parsers.agy import AgyParser


def test_plain_text_line_yields_text_event() -> None:
    """A single non-JSON line 'hello' yields one AgentOutputLine(type='text')."""
    parser = AgyParser()
    lines = ["hello"]
    parsed = list(parser.parse(iter(lines)))

    assert len(parsed) == 1
    line = parsed[0]
    assert line.type == "text"
    assert line.content == "hello"
    assert line.raw == "hello"


def test_two_consecutive_text_lines_coalesce_into_one_text_event() -> None:
    """Two consecutive plain-text lines coalesce into one text event via TextAccumulator."""
    parser = AgyParser()
    lines = [
        "I will create the todo list implementation.",
        "Using module.exports for CommonJS compatibility.",
    ]
    parsed = list(parser.parse(iter(lines)))

    assert len(parsed) == 1
    line = parsed[0]
    assert line.type == "text"
    expected_content = (
        "I will create the todo list implementation.\n"
        "Using module.exports for CommonJS compatibility."
    )
    assert line.content == expected_content


def test_task_declared_complete_line_yields_text_event() -> None:
    """Marker line yields one text event with the marker as content."""
    parser = AgyParser()
    lines = ["Task declared complete:"]
    parsed = list(parser.parse(iter(lines)))

    assert len(parsed) == 1
    line = parsed[0]
    assert line.type == "text"
    assert line.content == "Task declared complete:"
    assert line.raw == "Task declared complete:"


def test_empty_input_produces_zero_events() -> None:
    """An empty input iterator yields zero AgentOutputLine objects."""
    parser = AgyParser()
    lines: list[str] = []
    parsed = list(parser.parse(iter(lines)))

    assert parsed == []
