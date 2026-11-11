from __future__ import annotations

from pathlib import Path

import pytest

from ralph.agents.parsers.agy import AgyParser
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.pipeline.plumbing.smoke_plumbing import (
    _count_parsed_events,
    _meaningful_output_lines,
)

_FIXTURES_DIR = Path(__file__).parent / "display" / "_fixtures"
_AGY_WIRE_FIXTURE = _FIXTURES_DIR / "agy_wire.jsonl"
_AGY_WIRE_TOOL_FIXTURE = _FIXTURES_DIR / "agy_wire_tool.jsonl"
_AGY_WIRE_SUBAGENT_FIXTURE = _FIXTURES_DIR / "agy_wire_subagent.jsonl"
_AGY_WIRE_TEXT_FIXTURE = _FIXTURES_DIR / "agy_wire_text.jsonl"
_AGY_WIRE_B_SERIES_FIXTURE = _FIXTURES_DIR / "agy_wire_b_series.jsonl"
_AGY_WIRE_V1_1_13_FIXTURE = _FIXTURES_DIR / "agy_wire_v1_1_13.jsonl"

pytestmark = pytest.mark.smoke


def _replay(fixture: Path) -> list:
    parser = AgyParser()
    lines = fixture.read_text(encoding="utf-8").splitlines()
    return list(parser.parse(iter(lines)))


def _fixture_lines(fixture: Path, indices: list[int]) -> list[str]:
    lines = fixture.read_text(encoding="utf-8").splitlines()
    return [lines[i] for i in indices]


def test_plain_text_line_yields_text_event() -> None:
    parser = AgyParser()
    lines = ["hello"]
    parsed = list(parser.parse(iter(lines)))

    assert len(parsed) == 1
    line = parsed[0]
    assert line.type == "text"
    assert line.content == "hello"
    assert line.raw == "hello"


def test_two_consecutive_text_lines_coalesce_into_one_text_event() -> None:
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
    parser = AgyParser()
    lines = ["Task declared complete:"]
    parsed = list(parser.parse(iter(lines)))

    assert len(parsed) == 1
    line = parsed[0]
    assert line.type == "text"
    assert line.content == "Task declared complete:"
    assert line.raw == "Task declared complete:"


def test_empty_input_produces_zero_events() -> None:
    parser = AgyParser()
    lines: list[str] = []
    parsed = list(parser.parse(iter(lines)))

    assert parsed == []


def test_stream_json_step_updates_emit_text_and_stop() -> None:
    parser = AgyParser()
    lines = [
        '{"event":"step_update","step_update":{"step_type":"agent_response","text_delta":"hello"}}',
        '{"event":"result","result":{"status":"SUCCESS"}}',
    ]

    parsed = list(parser.parse(iter(lines)))

    assert [(line.type, line.content) for line in parsed] == [
        ("text", "hello"),
        ("stop", "agy result SUCCESS"),
    ]


def test_agy_parser_regression_stream_json_transcript_is_not_collapsed() -> None:
    parser = AgyParser()
    lines = [
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
        ("stop", "agy result SUCCESS"),
    ]


def test_stream_json_subagent_updates_emit_correlated_events() -> None:
    parser = AgyParser()
    lines = [
        '{"event":"step_update","step_update":{"step_index":4,"step_type":"subagent","state":"ACTIVE","subagent_info":{"subagents":[{"role":"research"}]}}}',
        '{"event":"step_update","step_update":{"step_index":4,"step_type":"subagent","state":"DONE","subagent_info":{"subagents":[{"conversation_id":"call-1","role":"research"}]}}}',
        '{"event":"step_update","step_update":{"step_type":"agent_response","text_delta":"after"}}',
    ]

    parsed = list(parser.parse(iter(lines)))

    assert [
        (line.type, line.metadata.get("tool"), line.metadata.get("tool_use_id"))
        for line in parsed[:2]
    ] == [
        ("tool_use", "subagent", "4:0"),
        ("tool_result", "subagent", "4:0"),
    ]
    assert parsed[1].metadata.get("conversation_id") == "call-1"
    assert parsed[2].type == "text"
    assert parsed[2].content == "after"


def test_agy_parser_regression_stream_json_subagent_step_index_correlates_result() -> None:
    parser = AgyParser()
    lines = [
        '{"event":"step_update","step_update":{"step_index":6,"step_type":"subagent","state":"ACTIVE","subagent_info":{"subagents":[{"role":"research"}]}}}',
        '{"event":"step_update","step_update":{"step_index":6,"step_type":"subagent","state":"DONE","subagent_info":{"subagents":[{"conversation_id":"subagent-1","role":"research"}]}}}',
    ]

    parsed = list(parser.parse(iter(lines)))

    assert [(line.type, line.metadata.get("tool_use_id")) for line in parsed] == [
        ("tool_use", "6:0"),
        ("tool_result", "6:0"),
    ]
    assert parsed[1].metadata.get("conversation_id") == "subagent-1"


def test_agy_parser_regression_stream_json_tool_step_index_correlates_result() -> None:
    parser = AgyParser()
    lines = [
        '{"event":"step_update","step_update":{"step_index":3,"step_type":"tool","state":"ACTIVE","tool_info":{"name":"write_file"}}}',
        '{"event":"step_update","step_update":{"step_index":3,"step_type":"tool","state":"DONE","tool_info":{"name":"write_file","output":"written"}}}',
    ]

    parsed = list(parser.parse(iter(lines)))

    assert [
        (
            line.type,
            line.metadata.get("tool"),
            line.metadata.get("tool_use_id"),
            line.metadata.get("step_ordinal"),
        )
        for line in parsed
    ] == [
        ("tool_use", "write_file", None, "3"),
        ("tool_result", "write_file", None, "3"),
    ]


def test_step_update_deduplicates_tool_use_events() -> None:
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
    parser = AgyParser()
    lines = [
        '{"event":"result","result":{"status":"ERROR","error":"quota"}}',
    ]
    parsed = list(parser.parse(iter(lines)))
    assert [(line.type, line.content) for line in parsed] == [
        ("error", "quota"),
        ("stop", "agy result ERROR"),
    ]


def test_result_event_with_success_yields_stop_only() -> None:
    parser = AgyParser()
    lines = [
        '{"event":"result","result":{"status":"SUCCESS"}}',
    ]
    parsed = list(parser.parse(iter(lines)))
    assert [(line.type, line.content) for line in parsed] == [
        ("stop", "agy result SUCCESS"),
    ]


def test_unrecognized_stream_json_frame_surfaces_in_harness_output() -> None:
    config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)
    lines = ['{"event":"custom_frame","data":{"message":"custom payload"}}']

    meaningful = _meaningful_output_lines(config, lines)
    event_count = _count_parsed_events(config, lines)

    assert event_count == 1
    assert len(meaningful) == 1
    assert "custom payload" in meaningful[0]


def test_subagent_update_with_multiple_entries() -> None:
    parser = AgyParser()
    lines = [
        '{"event":"step_update","step_update":{"step_index":3,"step_type":"subagent","state":"ACTIVE","subagent_info":{"subagents":[{"conversation_id":"c1","role":"agent1"},{"conversation_id":"c2","role":"agent2"}]}}}',
        '{"event":"step_update","step_update":{"step_index":5,"step_type":"subagent","state":"ACTIVE","subagent_info":{"subagents":[]}}}',
        '{"event":"step_update","step_update":{"step_index":6,"step_type":"subagent","state":"ACTIVE","subagent_info":{"subagents":[{"conversation_id":"c3","role":"agent3"},{"conversation_id":"c4","role":"agent4"}]}}}',
        '{"event":"step_update","step_update":{"step_index":6,"step_type":"subagent","state":"DONE","subagent_info":{"subagents":[{"conversation_id":"c3","role":"agent3"},{"conversation_id":"c4","role":"agent4"}]}}}',
    ]
    parsed = list(parser.parse(iter(lines)))
    assert [(line.type, line.metadata.get("tool_use_id")) for line in parsed] == [
        ("tool_use", "3:0"),
        ("tool_use", "3:1"),
        ("lifecycle", None),
        ("tool_use", "6:0"),
        ("tool_use", "6:1"),
        ("tool_result", "6:0"),
        ("tool_result", "6:1"),
    ]
    empty_frame = parsed[2]
    assert empty_frame.content == "agy step subagent"
    assert parsed[0].metadata.get("conversation_id") == "c1"
    assert parsed[5].metadata.get("conversation_id") == "c3"


def test_subagent_update_multiple_entries_without_ids_use_positional_fallback() -> None:
    parser = AgyParser()
    lines = [
        '{"event":"step_update","step_update":{"step_index":9,"step_type":"subagent","state":"ACTIVE","subagent_info":{"subagents":[{"role":"agent1"},{"role":"agent2"}]}}}',
    ]
    parsed = list(parser.parse(iter(lines)))
    assert [(line.type, line.metadata.get("tool_use_id")) for line in parsed] == [
        ("tool_use", "9:0"),
        ("tool_use", "9:1"),
    ]


def test_text_delta_normalizes_vt_text() -> None:
    parser = AgyParser()
    lines = [
        '{"event":"step_update","step_update":{"step_type":"agent_response","text_delta":"\x1b[31mred text\x1b[0m"}}',
    ]
    parsed = list(parser.parse(iter(lines)))
    assert len(parsed) == 1
    assert parsed[0].type == "text"
    assert parsed[0].content == "red text"


def test_agy_parser_coalesces_plain_text_output_format_transcript() -> None:
    parser = AgyParser()
    lines = [
        "I will create the todo list implementation.",
        "Writing smoke_test_result artifact.",
    ]
    parsed = list(parser.parse(iter(lines)))

    assert len(parsed) == 1
    assert parsed[0].type == "text"
    assert parsed[0].content == (
        "I will create the todo list implementation.\nWriting smoke_test_result artifact."
    )


def test_agy_wire_fixture_replay_yields_expected_event_sequence() -> None:
    parsed = _replay(_AGY_WIRE_FIXTURE)

    types = [line.type for line in parsed]
    assert types == [
        "lifecycle",
        "lifecycle",
        "lifecycle",
        "text",
        "tool_use",
        "tool_result",
        "lifecycle",
        "tool_use",
        "tool_result",
        "text",
        "stop",
    ]
    assert parsed[0].content == "agy init gemini-3.6-flash-low"
    assert parsed[0].metadata.get("model") == "gemini-3.6-flash-low"
    assert parsed[1].content == "agy step user_input"
    assert parsed[2].content == "agy step unknown"
    assert parsed[3].content == "I will create the file and read it back."
    assert parsed[4].content == "write_to_file hello.txt"
    assert parsed[5].content == "write_to_file hello.txt (0.07s)"
    assert parsed[6].content == "agy step checkpoint"
    assert parsed[7].content == "view_file hello.txt"
    assert parsed[8].content == "2 lines, 3 bytes"
    assert parsed[9].content == "I have created the file and confirmed its contents."
    assert parsed[10].type == "stop"
    assert parsed[10].content == "agy result SUCCESS (3.58s, 1 turn)"


def test_d1_subagent_dispatch_and_result_ids_correlate() -> None:
    parsed = _replay(_AGY_WIRE_SUBAGENT_FIXTURE)

    subagent_use_ids = [
        line.metadata.get("tool_use_id")
        for line in parsed
        if line.type == "tool_use" and line.metadata.get("tool") == "subagent"
    ]
    subagent_result_ids = [
        line.metadata.get("tool_use_id")
        for line in parsed
        if line.type == "tool_result" and line.metadata.get("tool") == "subagent"
    ]
    assert len(subagent_use_ids) == 2
    assert len(subagent_result_ids) == 2
    assert subagent_use_ids == subagent_result_ids


def test_d2_subagent_events_identify_their_subagent() -> None:
    parsed = _replay(_AGY_WIRE_SUBAGENT_FIXTURE)

    active = [
        line
        for line in parsed
        if line.type == "tool_use" and line.metadata.get("tool") == "subagent"
    ]
    done = [
        line
        for line in parsed
        if line.type == "tool_result" and line.metadata.get("tool") == "subagent"
    ]
    assert [line.content for line in active] == ["Write File A", "Write File B"]
    assert active[0].metadata.get("type_name") == "file_writer"
    assert active[0].metadata.get("role") == "Write File A"
    assert active[0].metadata.get("conversation_id") is None
    assert done[0].metadata.get("conversation_id") == "00000000-0000-0000-0000-00000000000a"
    assert done[1].metadata.get("conversation_id") == "00000000-0000-0000-0000-00000000000b"


def test_d3_define_and_manage_subagent_stay_ordinary_tool_calls() -> None:
    parsed = _replay(_AGY_WIRE_SUBAGENT_FIXTURE)

    tool_names = {line.metadata.get("tool") for line in parsed if line.type == "tool_use"}
    assert "define_subagent" in tool_names
    assert "invoke_subagent" in tool_names
    assert "manage_subagents" in tool_names

    subagent_dispatch_count = len(
        [
            line
            for line in parsed
            if line.type == "tool_use" and line.metadata.get("tool") == "subagent"
        ]
    )
    assert subagent_dispatch_count == 2


def test_d4_write_to_file_done_yields_nonempty_tool_result() -> None:
    parsed = _replay(_AGY_WIRE_TOOL_FIXTURE)

    results = [
        line
        for line in parsed
        if line.type == "tool_result" and line.metadata.get("tool") == "write_to_file"
    ]
    assert len(results) == 1
    assert results[0].content != ""


def test_d5_write_to_file_tool_use_surfaces_target_file_parameter() -> None:
    parsed = _replay(_AGY_WIRE_TOOL_FIXTURE)

    uses = [
        line
        for line in parsed
        if line.type == "tool_use" and line.metadata.get("tool") == "write_to_file"
    ]
    assert len(uses) == 1
    assert "hello.txt" in uses[0].content


def test_d6_no_text_event_has_surrounding_whitespace_or_is_empty() -> None:
    for fixture in (_AGY_WIRE_TOOL_FIXTURE, _AGY_WIRE_SUBAGENT_FIXTURE, _AGY_WIRE_TEXT_FIXTURE):
        parsed = _replay(fixture)
        for line in parsed:
            if line.type == "text":
                assert line.content == line.content.strip()
                assert line.content != ""


def test_d7_init_frame_yields_observable_lifecycle_event() -> None:
    parsed = _replay(_AGY_WIRE_TOOL_FIXTURE)

    lifecycle_events = [line for line in parsed if line.type == "lifecycle"]
    assert len(lifecycle_events) > 1
    init_events = [line for line in lifecycle_events if "model" in line.metadata]
    assert len(init_events) == 1
    meta = init_events[0].metadata
    assert meta.get("model") == "gemini-3.6-flash-low"
    assert meta.get("cwd") == "/workspace"
    assert meta.get("permission_mode") == "always-proceed"
    assert "define_subagent" in meta.get("tools", [])


def test_d7_init_lifecycle_event_has_nonempty_content() -> None:
    for fixture in (_AGY_WIRE_TEXT_FIXTURE, _AGY_WIRE_TOOL_FIXTURE, _AGY_WIRE_SUBAGENT_FIXTURE):
        parsed = _replay(fixture)
        lifecycle_events = [line for line in parsed if line.type == "lifecycle"]
        assert lifecycle_events, f"expected at least one lifecycle event in {fixture.name}"
        for line in lifecycle_events:
            assert line.content.strip() != ""


def test_da002_stop_event_has_nonempty_content() -> None:
    for fixture in (_AGY_WIRE_TEXT_FIXTURE, _AGY_WIRE_TOOL_FIXTURE, _AGY_WIRE_SUBAGENT_FIXTURE):
        parsed = _replay(fixture)
        stop_events = [line for line in parsed if line.type == "stop"]
        assert stop_events, f"expected at least one stop event in {fixture.name}"
        for line in stop_events:
            assert line.content.strip() != ""


def test_d8_result_metadata_lifts_fields_to_top_level() -> None:
    parsed = _replay(_AGY_WIRE_TOOL_FIXTURE)

    stop_events = [line for line in parsed if line.type == "stop"]
    assert len(stop_events) == 1
    meta = stop_events[0].metadata
    assert meta.get("status") == "SUCCESS"
    assert meta.get("num_turns") == 1
    assert meta.get("duration_seconds") == 3.58
    assert meta.get("usage") == {
        "input_tokens": 5609,
        "output_tokens": 104,
        "total_tokens": 5713,
    }
    assert meta.get("result", {}).get("status") == "SUCCESS"


def test_d8_agent_response_done_usage_reaches_text_event_metadata() -> None:
    parsed = _replay(_AGY_WIRE_TOOL_FIXTURE)

    text_events = [line for line in parsed if line.type == "text"]
    assert text_events[-1].metadata.get("usage") == {
        "input_tokens": 5609,
        "output_tokens": 104,
        "total_tokens": 5713,
    }


def test_d9_error_event_without_error_key_yields_error_type() -> None:
    parser = AgyParser()
    lines = ['{"event":"error","message":"boom"}']
    parsed = list(parser.parse(iter(lines)))
    assert [(line.type, line.content) for line in parsed] == [("error", "boom")]


def test_d9_error_event_with_error_key_still_yields_error_type() -> None:
    parser = AgyParser()
    lines = ['{"event":"error","error":{"message":"boom"}}']
    parsed = list(parser.parse(iter(lines)))
    assert [(line.type, line.content) for line in parsed] == [("error", "boom")]


def test_b3_user_input_unknown_checkpoint_steps_emit_lifecycle_events() -> None:
    parser = AgyParser()
    lines = _fixture_lines(_AGY_WIRE_B_SERIES_FIXTURE, [1, 2, 6])
    parsed = list(parser.parse(iter(lines)))
    assert [(line.type, line.content) for line in parsed] == [
        ("lifecycle", "agy step user_input"),
        ("lifecycle", "agy step unknown"),
        ("lifecycle", "agy step checkpoint"),
    ]
    for line in parsed:
        assert line.content.strip() != ""


def test_sanity_crlf_line_endings_parse_identically_to_lf() -> None:
    body = '{"event":"step_update","step_update":{"step_type":"agent_response","text_delta":"hi"}}'
    result_line = '{"event":"result","result":{"status":"SUCCESS"}}'

    parsed_lf = list(AgyParser().parse(iter([body, result_line])))
    parsed_crlf = list(AgyParser().parse(iter([body + "\r\n", result_line + "\r\n"])))

    assert [(line.type, line.content) for line in parsed_lf] == [
        (line.type, line.content) for line in parsed_crlf
    ]


def test_b2_completion_summary_duration_rounds_to_two_decimals() -> None:
    parser = AgyParser()
    lines = _fixture_lines(_AGY_WIRE_B_SERIES_FIXTURE, [4, 5])
    parsed = list(parser.parse(iter(lines)))
    result = next(line for line in parsed if line.type == "tool_result")
    assert result.content == "write_to_file hello.txt (0.08s)"
    assert "0.075956764" not in result.content


def test_b2_result_summary_duration_rounds_to_two_decimals() -> None:
    parser = AgyParser()
    lines = _fixture_lines(_AGY_WIRE_B_SERIES_FIXTURE, [11])
    parsed = list(parser.parse(iter(lines)))
    stop = parsed[-1]
    assert stop.type == "stop"
    assert stop.content == "agy result SUCCESS (2.59s, 1 turn)"
    assert "2.586211531" not in stop.content


def test_b5_step_index_fallback_id_is_not_labeled_tool_use_id() -> None:
    parser = AgyParser()
    lines = _fixture_lines(_AGY_WIRE_B_SERIES_FIXTURE, [4, 5])
    parsed = list(parser.parse(iter(lines)))
    assert [line.metadata.get("tool_use_id") for line in parsed] == [None, None]
    assert [line.metadata.get("step_ordinal") for line in parsed] == ["3", "3"]


def test_b5_genuine_call_id_still_uses_tool_use_id_key() -> None:
    parser = AgyParser()
    lines = [
        '{"event":"step_update","step_update":{"step_index":9,"step_type":"tool","state":"ACTIVE","tool_info":{"name":"write_to_file","call_id":"real-call-42"}}}',
        '{"event":"step_update","step_update":{"step_index":9,"step_type":"tool","state":"DONE","tool_info":{"name":"write_to_file","call_id":"real-call-42","output":"written"}}}',
    ]
    parsed = list(parser.parse(iter(lines)))
    assert [line.metadata.get("tool_use_id") for line in parsed] == [
        "real-call-42",
        "real-call-42",
    ]
    assert all(line.metadata.get("step_ordinal") is None for line in parsed)


def test_b3_bodiless_agent_response_done_with_no_buffered_text_surfaces_usage() -> None:
    parser = AgyParser()
    lines = [
        '{"event":"step_update","step_update":{"step_index":2,"step_type":"agent_response","state":"DONE","text_delta":"","usage":{"input_tokens":10,"output_tokens":2,"total_tokens":12}}}',
    ]
    parsed = list(parser.parse(iter(lines)))
    assert len(parsed) == 1
    assert parsed[0].type == "lifecycle"
    assert parsed[0].content.strip() != ""
    assert parsed[0].metadata.get("usage") == {
        "input_tokens": 10,
        "output_tokens": 2,
        "total_tokens": 12,
    }


def test_b4_bodiless_agent_response_done_usage_reaches_pending_flush() -> None:
    parsed = _replay(_AGY_WIRE_TEXT_FIXTURE)

    text_events = [line for line in parsed if line.type == "text"]
    assert len(text_events) == 1
    assert text_events[0].content == "OK"
    assert text_events[0].metadata.get("usage") == {
        "input_tokens": 120,
        "output_tokens": 3,
        "total_tokens": 123,
    }


def test_s2_back_to_back_bodiless_usage_frames_merge_instead_of_overwriting() -> None:
    parser = AgyParser()
    lines = [
        '{"event":"step_update","step_update":{"step_index":1,"state":"ACTIVE","step_type":"agent_response","text_delta":"Working"}}',
        '{"event":"step_update","step_update":{"step_index":2,"state":"DONE","step_type":"agent_response","text_delta":"","usage":{"input_tokens":10,"output_tokens":2,"total_tokens":12}}}',
        '{"event":"step_update","step_update":{"step_index":3,"state":"DONE","step_type":"agent_response","text_delta":"","usage":{"input_tokens":5,"output_tokens":1,"total_tokens":6}}}',
        '{"event":"result","result":{"status":"SUCCESS"}}',
    ]
    parsed = list(parser.parse(iter(lines)))
    text_events = [line for line in parsed if line.type == "text"]
    assert len(text_events) == 1
    assert text_events[0].content == "Working"
    assert text_events[0].metadata.get("usage") == {
        "input_tokens": 15,
        "output_tokens": 3,
        "total_tokens": 18,
    }


def test_b1_orphan_tool_result_record_body_names_tool_without_duplicating_it() -> None:
    from ralph.display.activity_provider import ActivityProvider
    from ralph.display.agent_event_renderer import normalize_event_from_agent_output_line
    from ralph.display.presented_entry import build_presented_entry

    parser = AgyParser()
    lines = _fixture_lines(_AGY_WIRE_B_SERIES_FIXTURE, [4, 5])
    parsed = list(parser.parse(iter(lines)))
    tool_result_line = next(line for line in parsed if line.type == "tool_result")

    assert tool_result_line.metadata.get("tool_use_id") is None
    assert tool_result_line.metadata.get("step_ordinal") == "3"

    event = normalize_event_from_agent_output_line(
        tool_result_line, provider=ActivityProvider.AGY, unit_id="agy"
    )
    entry = build_presented_entry(event, unit_id="agy")

    assert entry.body != ""
    assert entry.body.casefold().count("write_to_file") == 1
    assert "hello.txt" in entry.body
    assert "0.08s" in entry.body


def test_b1_correlated_tool_result_record_body_omits_tool_name_and_stays_nonempty() -> None:
    from ralph.display.activity_provider import ActivityProvider
    from ralph.display.agent_event_renderer import normalize_event_from_agent_output_line
    from ralph.display.presented_entry import build_presented_entry

    parser = AgyParser()
    lines = [
        '{"event":"step_update","step_update":{"step_index":3,"step_type":"tool","state":"ACTIVE","tool_info":{"name":"write_to_file","call_id":"real-1","parameters":{"TargetFile":"/workspace/todo-list.js"}}}}',
        '{"event":"step_update","step_update":{"step_index":3,"step_type":"tool","state":"DONE","tool_info":{"name":"write_to_file","call_id":"real-1","parameters":{"TargetFile":"/workspace/todo-list.js"}},"duration_seconds":0.08}}',
    ]
    parsed = list(parser.parse(iter(lines)))
    tool_result_line = next(line for line in parsed if line.type == "tool_result")
    assert tool_result_line.metadata.get("tool_use_id") == "real-1"

    event = normalize_event_from_agent_output_line(
        tool_result_line, provider=ActivityProvider.AGY, unit_id="agy"
    )
    entry = build_presented_entry(event, unit_id="agy")

    assert entry.body != ""
    assert "write_to_file" not in entry.body.casefold()
    assert "todo-list.js" in entry.body


def test_b6_bracketed_markdown_link_survives_to_the_rendered_activity_line() -> None:
    from ralph.display.activity_provider import ActivityProvider
    from ralph.display.agent_event_renderer import (
        normalize_event_from_agent_output_line,
        render_event_kind_text,
    )

    parser = AgyParser()
    lines = [
        '{"event":"step_update","step_update":{"step_index":5,"state":"ACTIVE","step_type":"agent_response","text_delta":"Full implementation at [todo"}}',
        '{"event":"step_update","step_update":{"step_index":5,"state":"DONE","step_type":"agent_response","text_delta":"-list.js](file:///workspace/todo-list.js)\\n"}}',
    ]
    parsed = list(parser.parse(iter(lines)))
    text_line = next(line for line in parsed if line.type == "text")

    assert "[todo-list.js](file:///workspace/todo-list.js)" in text_line.content

    event = normalize_event_from_agent_output_line(
        text_line, provider=ActivityProvider.AGY, unit_id="agy"
    )
    rendered = render_event_kind_text(
        event.kind, event.content, metadata=event.metadata, agent_name="agy"
    )
    assert "[todo-list.js](file:///workspace/todo-list.js)" in rendered


def test_v1_1_13_system_message_step_surfaces_as_lifecycle_event() -> None:
    parsed = _replay(_AGY_WIRE_V1_1_13_FIXTURE)
    system_messages = [
        line for line in parsed if line.metadata.get("step_type") == "system_message"
    ]
    assert len(system_messages) == 2
    assert all(line.type == "lifecycle" for line in system_messages)
    assert all(line.content == "agy step system_message" for line in system_messages)
    assert all(line.raw.strip().startswith("{") for line in system_messages)


def test_future_unknown_bodiless_step_type_surfaces_as_lifecycle_event() -> None:
    parser = AgyParser()
    lines = [
        '{"event":"step_update","step_update":{"conversation_id":"c","step_index":9,"state":"DONE","step_type":"telemetry_notice"}}',
    ]
    parsed = list(parser.parse(iter(lines)))
    assert [line.type for line in parsed] == ["lifecycle"]
    assert parsed[0].content == "agy step telemetry_notice"
    assert parsed[0].metadata.get("step_type") == "telemetry_notice"


def test_v1_1_13_subagent_active_entries_carry_identity_and_workspace_uris() -> None:
    parsed = _replay(_AGY_WIRE_V1_1_13_FIXTURE)
    active = [
        line
        for line in parsed
        if line.type == "tool_use" and line.metadata.get("tool") == "subagent"
    ]
    done = [
        line
        for line in parsed
        if line.type == "tool_result" and line.metadata.get("tool") == "subagent"
    ]
    assert [line.content for line in active] == ["File Writer A", "File Writer B"]
    assert active[0].metadata.get("conversation_id") == "00000000-0000-0000-0000-000000000002"
    assert active[1].metadata.get("conversation_id") == "00000000-0000-0000-0000-000000000003"
    assert [line.metadata.get("tool_use_id") for line in active] == ["6:0", "6:1"]
    assert [line.metadata.get("tool_use_id") for line in done] == ["6:0", "6:1"]
    assert done[0].metadata["tool_info"]["workspace_uris"] == ["file:///workspace"]
