"""Unit tests for the AgyParser.

Every wire-format claim in this file is grounded in
``ralph-workflow/tmp/agy-source-of-truth.txt`` (AGY v1.0.8 --print mode):

  * AGY --print emits plain-text model responses on stdout, one line at a time.
  * There is no native --print completion marker emitted by AGY itself; the
    prompt instructs the model to write a file and emit the
    ``Task declared complete:`` marker so the smoke detector can see it.
  * The documented failure mode on quota exhaustion is exit code 0 with empty
    stdout (issue #76).

The AgyParser must classify plain-text --print output as ``type='text'`` (NOT
``type='raw'``) so the smoke report's "Observed output:" section renders model
content via ``_render_text_line`` instead of the literal ``raw`` type label
via ``_render_metadata_event_line`` (see
``ralph-workflow/ralph/pipeline/activity_stream.py``).
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
        "I will create the todo list implementation."
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
