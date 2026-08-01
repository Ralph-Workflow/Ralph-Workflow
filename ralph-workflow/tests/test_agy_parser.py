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


def test_stream_json_step_updates_emit_text_and_stop() -> None:
    """S-3: AGY's measured stream-json text delta is observable."""
    parser = AgyParser()
    lines = [
        '{"event":"init","conversation_id":"conv-1"}',
        '{"event":"step_update","step_update":{"step_type":"agent_response","text_delta":"hello"}}',
        '{"event":"result","result":{"status":"SUCCESS"}}',
    ]

    parsed = list(parser.parse(iter(lines)))

    assert [(line.type, line.content) for line in parsed] == [("text", "hello"), ("stop", "")]


def test_agy_parser_regression_stream_json_transcript_is_not_collapsed() -> None:
    """S-2: mock stream-json keeps text and correlated tool activity distinct."""
    parser = AgyParser()
    lines = [
        '{"event":"init","conversation_id":"conv-1"}',
        '{"event":"step_update","step_update":{"step_type":"agent_response","text_delta":"creating artifact"}}',
        '{"event":"step_update","step_update":{"step_index":1,"step_type":"tool","state":"ACTIVE","tool_info":{"name":"write_to_file"}}}',
        '{"event":"step_update","step_update":{"step_index":1,"step_type":"tool","state":"DONE","tool_info":{"name":"write_to_file","output":"artifact written"}}}',
        '{"event":"result","result":{"status":"SUCCESS"}}',
    ]

    parsed = list(parser.parse(iter(lines)))

    assert [(line.type, line.content) for line in parsed] == [
        ("text", "creating artifact"),
        ("tool_use", "write_to_file"),
        ("tool_result", "artifact written"),
        ("stop", ""),
    ]


def test_stream_json_subagent_updates_emit_correlated_events() -> None:
    """S-3: tool and subagent updates preserve tool name and call id."""
    parser = AgyParser()
    lines = [
        '{"event":"step_update","step_update":{"step_type":"subagent","state":"ACTIVE","subagent_info":{"subagents":[{"conversation_id":"call-1","role":"research"}]}}}',
        '{"event":"step_update","step_update":{"step_type":"subagent","state":"DONE","subagent_info":{"subagents":[{"conversation_id":"call-1","role":"research"}]}}}',
        '{"event":"step_update","step_update":{"step_type":"agent_response","text_delta":"after"}}',
    ]

    parsed = list(parser.parse(iter(lines)))

    assert [
        (line.type, line.metadata.get("tool"), line.metadata.get("tool_use_id"))
        for line in parsed[:2]
    ] == [
        ("tool_use", "subagent", "call-1"),
        ("tool_result", "subagent", "call-1"),
    ]
    assert parsed[2].type == "text"
    assert parsed[2].content == "after"


def test_agy_parser_regression_stream_json_subagent_step_index_correlates_result() -> None:
    """S-3: live AGY adds conversation_id only to the DONE subagent update."""
    parser = AgyParser()
    lines = [
        '{"event":"step_update","step_update":{"step_index":6,"step_type":"subagent","state":"ACTIVE","subagent_info":{"subagents":[{"role":"research"}]}}}',
        '{"event":"step_update","step_update":{"step_index":6,"step_type":"subagent","state":"DONE","subagent_info":{"subagents":[{"conversation_id":"subagent-1","role":"research"}]}}}',
    ]

    parsed = list(parser.parse(iter(lines)))

    assert [(line.type, line.metadata.get("tool_use_id")) for line in parsed] == [
        ("tool_use", "6"),
        ("tool_result", "6"),
    ]


def test_agy_parser_regression_stream_json_tool_step_index_correlates_result() -> None:
    """S-3: v1.1.9 tool updates use step_index, not a call_id."""
    parser = AgyParser()
    lines = [
        '{"event":"step_update","step_update":{"step_index":3,"step_type":"tool","state":"ACTIVE","tool_info":{"name":"write_file"}}}',
        '{"event":"step_update","step_update":{"step_index":3,"step_type":"tool","state":"DONE","tool_info":{"name":"write_file","output":"written"}}}',
    ]

    parsed = list(parser.parse(iter(lines)))

    assert [
        (line.type, line.metadata.get("tool"), line.metadata.get("tool_use_id")) for line in parsed
    ] == [
        ("tool_use", "write_file", "3"),
        ("tool_result", "write_file", "3"),
    ]
