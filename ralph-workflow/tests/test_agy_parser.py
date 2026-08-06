"""Unit tests for the AgyParser.

The v1.1.10 source record at ``ralph-workflow/tmp/agy-source-of-truth.txt``
documents the installed binary's flags and model IDs from non-paid probes
(``--version``, ``--help``, ``models``, ``agents``). The live paid
stream-json probe was not run because live AGY is unauthenticated on the
measurement host, so the stream-json expectations below are driven by the
deterministic mock (``tests/_support/mock_agy.py``) and the CLI's documented
``--print --output-format stream-json`` interface rather than a captured
live transcript.

The AgyParser maps stream-json events (``init``, ``step_update``, ``result``)
to normalized ``text`` / ``tool_use`` / ``tool_result`` / ``error`` / ``stop``
events, and classifies plain-text fallback output as ``type='text'`` (not
``'raw'``) so the smoke report renders model content as text.
"""

from __future__ import annotations

from pathlib import Path

from ralph.agents.parsers.agy import AgyParser
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.pipeline.plumbing.smoke_plumbing import (
    _count_parsed_events,
    _meaningful_output_lines,
)

_AGY_WIRE_FIXTURE = Path(__file__).parent / "display" / "_fixtures" / "agy_wire.jsonl"


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


def test_step_update_deduplicates_tool_use_events() -> None:
    """P-2: Step updates sharing step_index in PENDING -> ACTIVE -> DONE yield 1 tool_use and 1 tool_result."""
    parser = AgyParser()
    lines = [
        '{"event":"step_update","step_update":{"step_index":3,"step_type":"tool","state":"PENDING","tool_info":{"name":"write_file"}}}',
        '{"event":"step_update","step_update":{"step_index":3,"step_type":"tool","state":"ACTIVE","tool_info":{"name":"write_file"}}}',
        '{"event":"step_update","step_update":{"step_index":3,"step_type":"tool","state":"DONE","tool_info":{"name":"write_file","output":"written"}}}',
    ]
    parsed = list(parser.parse(iter(lines)))
    assert [(line.type, line.content) for line in parsed] == [
        ("tool_use", "write_file"),
        ("tool_result", "written"),
    ]


def test_result_event_with_error_surfaces_error_event() -> None:
    """P-3: Error result surfaces error event before stop."""
    parser = AgyParser()
    lines = [
        '{"event":"result","result":{"status":"ERROR","error":"quota"}}',
    ]
    parsed = list(parser.parse(iter(lines)))
    assert [(line.type, line.content) for line in parsed] == [
        ("error", "quota"),
        ("stop", ""),
    ]


def test_result_event_with_success_yields_stop_only() -> None:
    """P-3: Success result surfaces stop event only."""
    parser = AgyParser()
    lines = [
        '{"event":"result","result":{"status":"SUCCESS"}}',
    ]
    parsed = list(parser.parse(iter(lines)))
    assert [(line.type, line.content) for line in parsed] == [
        ("stop", ""),
    ]


def test_unrecognized_stream_json_frame_surfaces_in_harness_output() -> None:
    """P-4: Unrecognized stream-json frames surface as meaningful text output events with payload."""
    config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)
    lines = ['{"event":"custom_frame","data":{"message":"custom payload"}}']

    meaningful = _meaningful_output_lines(config, lines)
    event_count = _count_parsed_events(config, lines)

    assert event_count == 1
    assert len(meaningful) == 1
    assert "custom payload" in meaningful[0]


def test_subagent_update_with_multiple_entries() -> None:
    """P-5: Subagent update with 2 entries emits correlated events for both, empty list emits none."""
    parser = AgyParser()
    lines = [
        '{"event":"step_update","step_update":{"step_type":"subagent","state":"ACTIVE","subagent_info":{"subagents":[{"conversation_id":"c1","role":"agent1"},{"conversation_id":"c2","role":"agent2"}]}}}',
        '{"event":"step_update","step_update":{"step_type":"subagent","state":"ACTIVE","subagent_info":{"subagents":[]}}}',
    ]
    parsed = list(parser.parse(iter(lines)))
    assert [(line.type, line.metadata.get("tool_use_id")) for line in parsed] == [
        ("tool_use", "c1"),
        ("tool_use", "c2"),
    ]


def test_text_delta_normalizes_vt_text() -> None:
    """P-6: text_delta containing ANSI escape codes is normalized."""
    parser = AgyParser()
    lines = [
        '{"event":"step_update","step_update":{"step_type":"agent_response","text_delta":"\x1b[31mred text\x1b[0m"}}',
    ]
    parsed = list(parser.parse(iter(lines)))
    assert len(parsed) == 1
    assert parsed[0].type == "text"
    assert parsed[0].content == "red text"


def test_agy_wire_fixture_replay_yields_expected_event_sequence() -> None:
    """Replay agy_wire.jsonl through AgyParser and assert normalized event sequence."""
    parser = AgyParser()
    lines = _AGY_WIRE_FIXTURE.read_text(encoding="utf-8").splitlines()
    parsed = list(parser.parse(iter(lines)))

    types = [line.type for line in parsed]
    assert types == [
        "text",
        "tool_use",
        "tool_result",
        "tool_use",
        "tool_use",
        "tool_result",
        "tool_result",
        "text",
        "error",
        "stop",
    ]
    assert parsed[0].content == "I will create the todo list implementation.\n"
    assert parsed[1].content == "createTodoList"
    assert parsed[2].content == "File created at tmp/interactive-agy-smoke/todo-list.js."
    assert parsed[7].content == "Writing smoke_test_result artifact.\n"
    assert parsed[8].content == "quota"

