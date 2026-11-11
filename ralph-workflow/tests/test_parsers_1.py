from __future__ import annotations

import json
import json as _json
from typing import TYPE_CHECKING

import pytest

from ralph.agents.parsers import (
    ClaudeParser,
    CodexParser,
    GeminiParser,
    GenericParser,
    OpenCodeParser,
    get_parser,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

EXPECTED_TWO_LINES = 2


def _make_lines(data: list[str]) -> Iterator[str]:
    return iter(data)


def test_claude_parser_content_block_delta() -> None:
    parser = ClaudeParser()
    lines = [
        '{"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}',
        '{"type":"content_block_delta","delta":{"type":"text_delta","text":" World"}}',
    ]
    results = list(parser.parse(_make_lines(lines)))

    text_results = [result for result in results if result.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "Hello World"


def test_claude_parser_message_stop() -> None:
    parser = ClaudeParser()
    lines = [
        '{"type":"message_start","message":{"id":"123"}}',
        '{"type":"content_block_start","content_block":{"type":"text"}}',
        '{"type":"content_block_delta","delta":{"type":"text_delta","text":"Hello"}}',
        '{"type":"content_block_stop"}',
        '{"type":"message_stop"}',
    ]
    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "text"
    assert results[0].content == "Hello"


def test_claude_parser_error() -> None:
    parser = ClaudeParser()
    lines = ['{"type":"error","error":{"type":"rate_limit_error","message":"Rate limited"}}']
    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "error"
    assert results[0].content == "Rate limited"


def test_claude_interactive_parser_error_via_public_factory() -> None:
    parser = get_parser("claude_interactive")
    lines = [
        (
            '{"type":"error","error":{"type":"rate_limit_error",'
            '"message":"You\'ve hit your session limit · resets 3:45pm"}}'
        )
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "error"
    assert results[0].content == "You've hit your session limit · resets 3:45pm"


def test_claude_parser_invalid_json() -> None:
    parser = ClaudeParser()
    lines = ["not valid json", '{"type":"content_block_delta","delta":{"text":"Hello"}}']
    results = list(parser.parse(_make_lines(lines)))

    assert results[0].type == "raw"
    assert results[0].content == "not valid json"
    assert results[1].type == "text"


def test_claude_parser_stream_event_wrapper_for_ccs() -> None:
    parser = ClaudeParser()
    lines = [
        (
            '{"type":"stream_event","event":{"type":"content_block_delta",'
            '"delta":{"type":"text_delta","text":"Hello from stream"}}}'
        ),
        '{"type":"stream_event","event":{"type":"message_stop"}}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "text"
    assert results[0].content == "Hello from stream"


def test_claude_parser_assistant_message_content_blocks() -> None:
    parser = ClaudeParser()
    lines = [
        '{"type":"assistant","message":{"content":[{"type":"text","text":"Final response"}]}}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "text"
    assert results[0].content == "Final response"


def test_claude_parser_result_event_does_not_emit_extra_stop_noise() -> None:
    parser = ClaudeParser()
    lines = [
        '{"type":"result","result":"Final answer"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "text"
    assert results[0].content == "Final answer"


def test_claude_parser_tool_result_keeps_structured_metadata_for_renderer_summary() -> None:
    parser = ClaudeParser()
    lines = [
        (
            '{"type":"assistant","message":{"content":[{"type":"tool_result",'
            '"content":[{"type":"text","text":"file content"}],'
            '"tool_use_id":"toolu_123","name":"read"}]}}'
        ),
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "tool_result"
    assert results[0].content == "file content"
    assert results[0].metadata["name"] == "read"


def test_claude_parser_tool_use_block_is_emitted_as_user_visible_tool_activity() -> None:
    parser = ClaudeParser()
    lines = [
        (
            '{"type":"content_block_start","content_block":{"type":"tool_use",'
            '"name":"bash","input":{"command":"ls -la","workdir":"/tmp"}}}'
        ),
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "tool_use"
    assert results[0].content == "bash"
    assert results[0].metadata["input"] == {"command": "ls -la", "workdir": "/tmp"}


def test_claude_parser_prefixed_transcript_user_message_extracts_tool_result() -> None:
    parser = ClaudeParser()
    lines = [
        (
            'claude user: message={"content":[{"content":"Task: produce a spec-compliant '
            'MCP commit_message artifact","tool_use_id":"toolu_123","type":"tool_result"}],'
            '"role":"user"}'
        ),
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "tool_result"
    assert results[0].content == "Task: produce a spec-compliant MCP commit_message artifact"
    assert results[0].metadata["tool_use_id"] == "toolu_123"


def test_claude_parser_prefixed_transcript_message_delta_is_suppressed() -> None:
    parser = ClaudeParser()
    lines = [
        (
            'claude message_delta: type=message_delta; delta={"stop_reason":"tool_use"}; '
            'usage={"output_tokens":170}; context_management={"applied_edits":[]}'
        ),
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert results == []


def test_opencode_parser_stream() -> None:
    parser = OpenCodeParser()
    lines = [
        '{"type":"step_start","id":"step-1"}',
        '{"type":"stream","content":"Hello"}',
        '{"type":"stream","content":" World"}',
        '{"type":"step_finish","id":"step-1"}',
        '{"type":"done"}',
    ]
    results = list(parser.parse(_make_lines(lines)))

    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "Hello World"


def test_opencode_parser_tool_use() -> None:
    parser = OpenCodeParser()
    lines = [
        '{"type":"tool_use","part":{"tool":"bash","input":{"command":"ls","workdir":"/repo"}}}',
    ]
    results = list(parser.parse(_make_lines(lines)))

    assert results[0].type == "tool_use"
    assert results[0].content == "bash"
    assert results[0].metadata["input"] == {"command": "ls", "workdir": "/repo"}


def test_opencode_parser_text_event_with_part_payload() -> None:
    parser = OpenCodeParser()
    lines = [
        '{"type":"text","part":{"type":"text","text":"Nested OpenCode text"}}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "text"
    assert results[0].content == "Nested OpenCode text"


def test_opencode_parser_tool_use_completed_state_emits_dispatch_then_result() -> None:
    parser = OpenCodeParser()
    lines = [
        (
            '{"type":"tool_use","part":{"tool":"read","state":'
            '{"status":"completed","output":"file content"}}}'
        ),
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert [r.type for r in results] == ["tool_use", "tool_result"]
    assert results[0].content == "read"
    assert results[1].content == "file content"
    assert results[1].metadata["tool"] == "read"


def test_opencode_parser_tool_result_preserves_tool_context_and_structured_metadata() -> None:
    parser = OpenCodeParser()
    lines = [
        (
            '{"type":"tool_result","tool":"grep","result":{"matches":3,"path":"src"},'
            '"part":{"tool":"grep","input":{"pattern":"TODO"}}}'
        ),
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "tool_result"
    assert results[0].content == "{'matches': 3, 'path': 'src'}"
    assert results[0].metadata["tool"] == "grep"
    assert results[0].metadata["input"] == {"pattern": "TODO"}


def test_generic_parser_content_fields() -> None:
    parser = GenericParser()
    lines = [
        '{"content":"Hello World"}',
        '{"type":"done"}',
        '{"text":"Another message"}',
        '{"type":"done"}',
        '{"message":"Third message"}',
        '{"type":"done"}',
    ]
    results = list(parser.parse(_make_lines(lines)))

    text_results = [result for result in results if result.type == "text"]
    assert text_results[0].content == "Hello World"
    assert text_results[1].content == "Another message"
    assert text_results[2].content == "Third message"


def test_generic_parser_error_detection() -> None:
    parser = GenericParser()
    lines = [
        '{"type":"error","error":"Something went wrong"}',
        '{"error":{"message":"Another error"}}',
    ]
    results = list(parser.parse(_make_lines(lines)))

    assert results[0].type == "error"
    assert "Something went wrong" in results[0].content
    assert results[1].type == "error"


def test_generic_parser_stop_markers() -> None:
    parser = GenericParser()
    lines = [
        '{"type":"stop"}',
        '{"type":"done"}',
        '{"type":"complete"}',
    ]
    results = list(parser.parse(_make_lines(lines)))

    assert all(r.type == "stop" for r in results)


def test_get_parser_claude() -> None:
    parser = get_parser("claude")
    assert isinstance(parser, ClaudeParser)


def test_get_parser_opencode() -> None:
    parser = get_parser("opencode")
    assert isinstance(parser, OpenCodeParser)


def test_get_parser_generic() -> None:
    parser = get_parser("generic")
    assert isinstance(parser, GenericParser)


def test_get_parser_codex() -> None:
    parser = get_parser("codex")
    assert isinstance(parser, CodexParser)


def test_codex_parser_dot_event_item_completed_agent_message() -> None:
    parser = CodexParser()
    lines = [
        '{"type":"item.completed","item":{"type":"agent_message","text":"Codex says hi"}}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "text"
    assert results[0].content == "Codex says hi"


def test_codex_parser_text_delta_supports_delta_text_field() -> None:
    parser = CodexParser()
    lines = [
        '{"type":"text_delta","delta":{"text":"Delta text field"}}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "text"
    assert results[0].content == "Delta text field"


def test_codex_parser_response_output_text_delta_is_parsed_as_text() -> None:
    parser = CodexParser()
    lines = [
        '{"type":"response.output_text.delta","delta":"streamed thought"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "text"
    assert results[0].content == "streamed thought"


def test_codex_parser_accepts_sse_data_prefix_lines() -> None:
    parser = CodexParser()
    lines = [
        'data: {"type":"text_delta","delta":"prefixed text"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "text"
    assert results[0].content == "prefixed text"


def test_codex_parser_regression_same_tool_calls_keep_distinct_call_ids() -> None:
    parser = CodexParser()
    lines = [
        '{"type":"item.started","item":{"id":"call-1","type":"mcp_tool_call","tool":"search_files","arguments":{"path":"ralph"}}}',
        '{"type":"item.started","item":{"id":"call-2","type":"mcp_tool_call","tool":"search_files","arguments":{"path":"ralph"}}}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert [result.metadata["tool_call_id"] for result in results] == ["call-1", "call-2"]


def test_codex_parser_item_completed_mcp_tool_result_maps_to_tool_result() -> None:
    parser = CodexParser()
    lines = [
        (
            '{"type":"item.completed","item":{'
            '"type":"mcp_tool_result",'
            '"tool":"write_memory",'
            '"result":{"status":"ok","written":2}'
            "}}"
        ),
    ]

    results = list(parser.parse(_make_lines(lines)))

    assert len(results) == 1
    assert results[0].type == "tool_result"
    assert results[0].metadata["tool"] == "write_memory"
    assert results[0].metadata["result"] == {"status": "ok", "written": 2}


def test_codex_parser_delta_accumulates_to_single_line_on_stop() -> None:
    parser = CodexParser()
    lines = [
        '{"type":"text_delta","delta":"Hello","response_id":"resp-1"}',
        '{"type":"text_delta","delta":" ","response_id":"resp-1"}',
        '{"type":"text_delta","delta":"World","response_id":"resp-1"}',
        '{"type":"text_delta","delta":"!","response_id":"resp-1"}',
        '{"type":"response.completed","response_id":"resp-1"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "Hello World!"


def test_codex_parser_paragraph_boundary_yields_two_lines() -> None:
    parser = CodexParser()
    lines = [
        '{"type":"text_delta","delta":"First paragraph","response_id":"resp-1"}',
        '{"type":"text_delta","delta":"\n\n","response_id":"resp-1"}',
        '{"type":"text_delta","delta":"Second paragraph","response_id":"resp-1"}',
        '{"type":"response.completed","response_id":"resp-1"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == EXPECTED_TWO_LINES
    assert text_results[0].content == "First paragraph"
    assert text_results[1].content == "Second paragraph"


def test_codex_parser_error_delta_flushes_immediately() -> None:
    parser = CodexParser()
    lines = [
        '{"type":"text_delta","delta":"Some text","response_id":"resp-1"}',
        '{"type":"error","error":{"message":"Rate limited"}}',
        '{"type":"response.completed","response_id":"resp-1"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    text_results = [r for r in results if r.type == "text"]
    error_results = [r for r in results if r.type == "error"]
    assert len(text_results) == 1
    assert text_results[0].content == "Some text"
    assert len(error_results) == 1
    assert error_results[0].content == "Rate limited"


def test_codex_parser_iterator_exhaustion_flushes_accumulator() -> None:
    parser = CodexParser()
    lines = [
        '{"type":"text_delta","delta":"Partial","response_id":"resp-1"}',
        '{"type":"text_delta","delta":" text","response_id":"resp-1"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "Partial text"


def test_opencode_parser_delta_accumulates_to_single_line_on_done() -> None:
    parser = OpenCodeParser()
    lines = [
        '{"type":"step_start","id":"step-1"}',
        '{"type":"stream","content":"Hello"}',
        '{"type":"stream","content":" "}',
        '{"type":"stream","content":"World"}',
        '{"type":"step_finish","id":"step-1"}',
        '{"type":"done"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "Hello World"


def test_opencode_parser_paragraph_boundary_yields_two_lines() -> None:
    parser = OpenCodeParser()
    lines = [
        '{"type":"step_start","id":"step-1"}',
        '{"type":"stream","content":"Para 1\n\n"}',
        '{"type":"stream","content":"Para 2"}',
        '{"type":"step_finish","id":"step-1"}',
        '{"type":"done"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == EXPECTED_TWO_LINES
    assert text_results[0].content == "Para 1"
    assert text_results[1].content == "Para 2"


def test_opencode_parser_error_flushes_immediately() -> None:
    parser = OpenCodeParser()
    lines = [
        '{"type":"step_start","id":"step-1"}',
        '{"type":"stream","content":"Some text"}',
        '{"type":"error","error":{"message":"Tool failed"}}',
        '{"type":"step_finish","id":"step-1"}',
        '{"type":"done"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    text_results = [r for r in results if r.type == "text"]
    error_results = [r for r in results if r.type == "error"]
    assert len(text_results) == 1
    assert text_results[0].content == "Some text"
    assert len(error_results) == 1
    assert error_results[0].content == "Tool failed"
    assert [(result.type, result.content) for result in results] == [
        ("text", "Some text"),
        ("error", "Tool failed"),
        ("stop", ""),
    ]


def test_opencode_parser_iterator_exhaustion_flushes_accumulator() -> None:
    parser = OpenCodeParser()
    lines = [
        '{"type":"step_start","id":"step-1"}',
        '{"type":"stream","content":"Partial"}',
        '{"type":"stream","content":" text"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "Partial text"


def test_gemini_parser_text_content_accumulates() -> None:
    parser = GeminiParser()
    lines = [
        'data: {"type":"text","content":"Hello"}',
        'data: {"type":"text","content":" World"}',
        'data: {"type":"done"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "Hello World"


def test_gemini_parser_paragraph_boundary_yields_two_lines() -> None:
    parser = GeminiParser()
    lines = [
        'data: {"type":"text","content":"Para 1\n\n"}',
        'data: {"type":"text","content":"Para 2"}',
        'data: {"type":"done"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == EXPECTED_TWO_LINES
    assert text_results[0].content == "Para 1"
    assert text_results[1].content == "Para 2"


def test_gemini_parser_block_content_accumulates() -> None:
    parser = GeminiParser()
    lines = [
        'data: {"type":"block","content":"Block 1"}',
        'data: {"type":"block","content":" Block 2"}',
        'data: {"type":"stop"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "Block 1 Block 2"


def test_gemini_parser_message_end_flushes_accumulator() -> None:
    parser = GeminiParser()
    lines = [
        'data: {"type":"text","content":"Final text"}',
        'data: {"type":"message_end"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "Final text"


def test_gemini_parser_iterator_exhaustion_flushes_accumulator() -> None:
    parser = GeminiParser()
    lines = [
        'data: {"type":"text","content":"Partial"}',
        'data: {"type":"text","content":" text"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "Partial text"


def test_gemini_parser_tool_call_emitted_separately() -> None:
    parser = GeminiParser()
    lines = [
        'data: {"type":"text","content":"Thinking..."}',
        'data: {"type":"tool_call","name":"bash","args":{"command":"ls"}}',
        'data: {"type":"done"}',
    ]

    results = list(parser.parse(_make_lines(lines)))

    text_results = [r for r in results if r.type == "text"]
    tool_results = [r for r in results if r.type == "tool_use"]
    assert len(text_results) == 1
    assert text_results[0].content == "Thinking..."
    assert len(tool_results) == 1
    assert tool_results[0].content == "bash"


def test_get_parser_gemini() -> None:
    parser = get_parser("gemini")
    assert isinstance(parser, GeminiParser)


def test_get_parser_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown parser type"):
        get_parser("unknown_parser")


def test_codex_lifecycle_noise_is_suppressed() -> None:
    parser = CodexParser()
    lines = [
        '{"type":"thread.started","id":"thread-1"}',
        '{"type":"turn.started","id":"turn-1"}',
        '{"type":"message_start","message":{"id":"msg-1"}}',
    ]
    results = list(parser.parse(_make_lines(lines)))
    leaking = [r for r in results if r.type in {"text", "error", "message_start"}]
    assert leaking == [], f"Lifecycle noise leaked: {leaking}"


def test_opencode_lifecycle_noise_does_not_emit_text_or_error() -> None:
    parser = OpenCodeParser()
    lines = [
        '{"type":"step_start","id":"step-1"}',
        '{"type":"step_finish","id":"step-1"}',
    ]
    results = list(parser.parse(_make_lines(lines)))
    leaking = [r for r in results if r.type in {"text", "error"}]
    assert leaking == [], f"Lifecycle noise leaked: {leaking}"


def test_gemini_lifecycle_noise_does_not_emit_text_or_error() -> None:
    parser = GeminiParser()
    lines = [
        'data: {"type":"done"}',
        'data: {"type":"stop"}',
        'data: {"type":"message_end"}',
    ]
    results = list(parser.parse(_make_lines(lines)))
    leaking = [r for r in results if r.type in {"text", "error"}]
    assert leaking == [], f"Lifecycle noise leaked: {leaking}"


def test_generic_lifecycle_noise_does_not_emit_text_or_error() -> None:
    parser = GenericParser()
    lines = [
        '{"type":"stop"}',
        '{"type":"done"}',
        '{"type":"complete"}',
    ]
    results = list(parser.parse(_make_lines(lines)))
    leaking = [r for r in results if r.type in {"text", "error"}]
    assert leaking == [], f"Lifecycle noise leaked: {leaking}"


def test_gemini_thread_started_is_suppressed() -> None:
    parser = GeminiParser()
    results = list(parser.parse(_make_lines(['data: {"type":"thread.started"}'])))
    assert results == [], f"Expected empty list for thread.started, got: {results}"


def test_gemini_turn_started_is_suppressed() -> None:
    parser = GeminiParser()
    results = list(parser.parse(_make_lines(['data: {"type":"turn.started"}'])))
    assert results == [], f"Expected empty list for turn.started, got: {results}"


def test_gemini_message_start_is_suppressed() -> None:
    parser = GeminiParser()
    results = list(parser.parse(_make_lines(['data: {"type":"message_start"}'])))
    assert results == [], f"Expected empty list for message_start, got: {results}"


def test_gemini_heartbeat_is_suppressed() -> None:
    parser = GeminiParser()
    results = list(parser.parse(_make_lines(['data: {"type":"heartbeat"}'])))
    assert results == [], f"Expected empty list for heartbeat, got: {results}"


def test_gemini_ping_is_suppressed() -> None:
    parser = GeminiParser()
    results = list(parser.parse(_make_lines(['data: {"type":"ping"}'])))
    assert results == [], f"Expected empty list for ping, got: {results}"


def test_gemini_ready_is_suppressed() -> None:
    parser = GeminiParser()
    results = list(parser.parse(_make_lines(['data: {"type":"ready"}'])))
    assert results == [], f"Expected empty list for ready, got: {results}"


def test_gemini_start_event_type_is_suppressed() -> None:
    parser = GeminiParser()
    results = list(parser.parse(_make_lines(['data: {"type":"start"}'])))
    assert results == [], f"Expected empty list for start event, got: {results}"


def test_gemini_all_lifecycle_event_types_suppressed_before_real_content() -> None:
    parser = GeminiParser()
    lines = [
        'data: {"type":"thread.started"}',
        'data: {"type":"turn.started"}',
        'data: {"type":"heartbeat"}',
        'data: {"type":"ping"}',
        'data: {"type":"ready"}',
        'data: {"type":"start"}',
        'data: {"type":"text","content":"real output"}',
        'data: {"type":"done"}',
    ]
    results = list(parser.parse(_make_lines(lines)))
    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "real output"
    leaking = [
        r
        for r in results
        if r.type in {"thread.started", "turn.started", "heartbeat", "ping", "ready", "start"}
    ]
    assert leaking == [], f"Lifecycle events leaked: {leaking}"


def test_opencode_heartbeat_is_suppressed() -> None:
    parser = OpenCodeParser()
    results = list(parser.parse(_make_lines(['{"type":"heartbeat"}'])))
    assert results == [], f"Expected empty list for heartbeat, got: {results}"


def test_opencode_ping_is_suppressed() -> None:
    parser = OpenCodeParser()
    results = list(parser.parse(_make_lines(['{"type":"ping"}'])))
    assert results == [], f"Expected empty list for ping, got: {results}"


def test_opencode_ready_is_suppressed() -> None:
    parser = OpenCodeParser()
    results = list(parser.parse(_make_lines(['{"type":"ready"}'])))
    assert results == [], f"Expected empty list for ready, got: {results}"


def test_generic_start_type_is_suppressed() -> None:
    parser = GenericParser()
    results = list(parser.parse(_make_lines(['{"type":"start"}'])))
    assert results == [], f"Expected empty list for type=start, got: {results}"


def test_generic_begin_type_is_suppressed() -> None:
    parser = GenericParser()
    results = list(parser.parse(_make_lines(['{"type":"begin"}'])))
    assert results == [], f"Expected empty list for type=begin, got: {results}"


def test_generic_ready_type_is_suppressed() -> None:
    parser = GenericParser()
    results = list(parser.parse(_make_lines(['{"type":"ready"}'])))
    assert results == [], f"Expected empty list for type=ready, got: {results}"


def test_generic_heartbeat_type_is_suppressed() -> None:
    parser = GenericParser()
    results = list(parser.parse(_make_lines(['{"type":"heartbeat"}'])))
    assert results == [], f"Expected empty list for type=heartbeat, got: {results}"


def test_generic_thread_started_type_is_suppressed() -> None:
    parser = GenericParser()
    results = list(parser.parse(_make_lines(['{"type":"thread.started"}'])))
    assert results == [], f"Expected empty list for thread.started, got: {results}"


def test_generic_non_json_content_line_is_not_suppressed() -> None:
    parser = GenericParser()
    results = list(parser.parse(_make_lines(["starting the analysis"])))
    raw_results = [r for r in results if r.type == "raw"]
    assert len(raw_results) == 1, f"Expected raw output for non-JSON text, got: {results}"
    assert raw_results[0].content == "starting the analysis"


def test_generic_long_content_with_start_word_is_not_suppressed() -> None:
    parser = GenericParser()
    results = list(parser.parse(_make_lines(['{"content":"starting the analysis phase"}'])))
    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert "starting the analysis phase" in text_results[0].content


def test_generic_all_lifecycle_types_suppressed_real_content_passes() -> None:
    parser = GenericParser()
    lines = [
        '{"type":"start"}',
        '{"type":"begin"}',
        '{"type":"ready"}',
        '{"type":"heartbeat"}',
        '{"type":"thread.started"}',
        '{"type":"turn.started"}',
        '{"type":"message_start"}',
        '{"content":"real content here","type":"data"}',
        '{"type":"done"}',
    ]
    results = list(parser.parse(_make_lines(lines)))
    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "real content here"


def test_codex_ping_is_suppressed() -> None:
    parser = CodexParser()
    results = list(parser.parse(_make_lines(['{"type":"ping"}'])))
    assert results == [], f"Expected empty, got: {results}"


def test_codex_heartbeat_is_suppressed() -> None:
    parser = CodexParser()
    results = list(parser.parse(_make_lines(['{"type":"heartbeat"}'])))
    assert results == [], f"Expected empty, got: {results}"


def test_codex_ready_is_suppressed() -> None:
    parser = CodexParser()
    results = list(parser.parse(_make_lines(['{"type":"ready"}'])))
    assert results == [], f"Expected empty, got: {results}"


def test_opencode_assistant_type_is_suppressed() -> None:
    parser = OpenCodeParser()
    results = list(parser.parse(_make_lines(['{"type":"assistant"}'])))
    assert results == [], f"Expected empty, got: {results}"


def test_opencode_user_type_is_suppressed() -> None:
    parser = OpenCodeParser()
    results = list(parser.parse(_make_lines(['{"type":"user"}'])))
    assert results == [], f"Expected empty, got: {results}"


def test_generic_message_stop_type_is_suppressed() -> None:
    parser = GenericParser()
    results = list(parser.parse(_make_lines(['{"type":"message_stop"}'])))
    assert results == [], f"Expected empty, got: {results}"


def test_generic_content_block_start_type_is_suppressed() -> None:
    parser = GenericParser()
    results = list(parser.parse(_make_lines(['{"type":"content_block_start"}'])))
    assert results == [], f"Expected empty, got: {results}"


def test_generic_content_block_stop_type_is_suppressed() -> None:
    parser = GenericParser()
    results = list(parser.parse(_make_lines(['{"type":"content_block_stop"}'])))
    assert results == [], f"Expected empty, got: {results}"


def test_generic_user_type_is_suppressed() -> None:
    parser = GenericParser()
    results = list(parser.parse(_make_lines(['{"type":"user"}'])))
    assert results == [], f"Expected empty, got: {results}"


def test_generic_assistant_type_is_suppressed() -> None:
    parser = GenericParser()
    results = list(parser.parse(_make_lines(['{"type":"assistant"}'])))
    assert results == [], f"Expected empty, got: {results}"


def test_generic_thinking_type_is_suppressed() -> None:
    parser = GenericParser()
    results = list(parser.parse(_make_lines(['{"type":"thinking"}'])))
    assert results == [], f"Expected empty, got: {results}"


def test_generic_message_delta_type_is_suppressed() -> None:
    parser = GenericParser()
    results = list(parser.parse(_make_lines(['{"type":"message_delta"}'])))
    assert results == [], f"Expected empty, got: {results}"


def test_claude_prefixed_assistant_is_suppressed() -> None:
    parser = ClaudeParser()
    results = list(parser.parse(_make_lines(["claude/sonnet: assistant"])))
    assert results == [], f"Expected empty, got: {results}"


def test_claude_prefixed_message_start_is_suppressed() -> None:
    parser = ClaudeParser()
    results = list(parser.parse(_make_lines(["claude/sonnet: message_start"])))
    assert results == [], f"Expected empty, got: {results}"


def test_claude_prefixed_message_stop_is_suppressed() -> None:
    parser = ClaudeParser()
    results = list(parser.parse(_make_lines(["claude/sonnet: message_stop"])))
    assert results == [], f"Expected empty, got: {results}"


def test_claude_prefixed_content_block_start_is_suppressed() -> None:
    parser = ClaudeParser()
    results = list(parser.parse(_make_lines(["claude/sonnet: content_block_start"])))
    assert results == [], f"Expected empty, got: {results}"


def test_claude_prefixed_content_block_stop_is_suppressed() -> None:
    parser = ClaudeParser()
    results = list(parser.parse(_make_lines(["claude/sonnet: content_block_stop"])))
    assert results == [], f"Expected empty, got: {results}"


def test_claude_parser_tool_result_with_image_block_emits_placeholder() -> None:

    parser = ClaudeParser()
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_123",
                        "content": [
                            {
                                "type": "image",
                                "source": {"media_type": "image/png", "data": "abc123"},
                            },
                        ],
                    },
                ],
            },
        }
    )
    results = list(parser.parse(iter([line])))
    assert any(r.type == "tool_result" for r in results), (
        f"Expected a tool_result line, got: {[r.type for r in results]}"
    )
    assert not any(r.type == "error" for r in results), (
        f"Got unexpected error line(s): {[r for r in results if r.type == 'error']}"
    )
    tool_results = [r for r in results if r.type == "tool_result"]
    assert any("[image:" in r.content for r in tool_results), (
        f"Expected '[image:' in tool_result content, got: {[r.content for r in tool_results]}"
    )


def test_claude_parser_tool_result_with_resource_reference_emits_placeholder() -> None:

    parser = ClaudeParser()
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_456",
                        "content": [
                            {
                                "type": "resource_reference",
                                "uri": "ralph://media/abc123",
                                "modality": "pdf",
                            },
                        ],
                    },
                ],
            },
        }
    )
    results = list(parser.parse(iter([line])))
    assert any(r.type == "tool_result" for r in results)
    assert not any(r.type == "error" for r in results)
    tool_results = [r for r in results if r.type == "tool_result"]
    assert any("[pdf: ralph://media/abc123]" in r.content for r in tool_results), (
        f"Expected '[pdf: ...]' in content, got: {[r.content for r in tool_results]}"
    )


def test_claude_parser_tool_result_with_mixed_text_and_image_preserves_text() -> None:

    parser = ClaudeParser()
    line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_789",
                        "content": [
                            {"type": "text", "text": "Analysis complete."},
                            {
                                "type": "image",
                                "source": {"media_type": "image/jpeg", "data": "xyz"},
                            },
                        ],
                    },
                ],
            },
        }
    )
    results = list(parser.parse(iter([line])))
    assert any(r.type == "tool_result" for r in results)
    assert not any(r.type == "error" for r in results)
    tool_results = [r for r in results if r.type == "tool_result"]
    combined = " ".join(r.content for r in tool_results)
    assert "Analysis complete." in combined
    assert "[image:" in combined


def test_claude_parser_tool_result_with_audio_resource_reference_emits_placeholder() -> None:

    parser = ClaudeParser()
    line = _json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_audio",
                        "content": [
                            {
                                "type": "resource_reference",
                                "uri": "ralph://media/audio-abc",
                                "modality": "audio",
                                "mimeType": "audio/mpeg",
                            },
                        ],
                    },
                ],
            },
        }
    )
    results = list(parser.parse(iter([line])))
    assert any(r.type == "tool_result" for r in results), (
        f"Expected tool_result, got: {[r.type for r in results]}"
    )
    assert not any(r.type == "error" for r in results), (
        f"Got unexpected error: {[r for r in results if r.type == 'error']}"
    )
    tool_results = [r for r in results if r.type == "tool_result"]
    assert any("[audio: ralph://media/audio-abc]" in r.content for r in tool_results), (
        f"Expected '[audio: ralph://media/audio-abc]' placeholder, "
        f"got: {[r.content for r in tool_results]}"
    )


def test_claude_parser_tool_result_with_video_resource_reference_emits_placeholder() -> None:

    parser = ClaudeParser()
    line = _json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_video",
                        "content": [
                            {
                                "type": "resource_reference",
                                "uri": "ralph://media/video-xyz",
                                "modality": "video",
                                "mimeType": "video/mp4",
                            },
                        ],
                    },
                ],
            },
        }
    )
    results = list(parser.parse(iter([line])))
    assert any(r.type == "tool_result" for r in results)
    assert not any(r.type == "error" for r in results)
    tool_results = [r for r in results if r.type == "tool_result"]
    assert any("[video: ralph://media/video-xyz]" in r.content for r in tool_results), (
        f"Expected '[video: ralph://media/video-xyz]' placeholder, "
        f"got: {[r.content for r in tool_results]}"
    )


def test_opencode_parser_tool_result_with_resource_reference_emits_placeholder() -> None:

    parser = OpenCodeParser()
    lines = [
        _json.dumps(
            {
                "type": "tool_result",
                "tool": "read_media",
                "result": [
                    {
                        "type": "resource_reference",
                        "uri": "ralph://media/aud-001",
                        "modality": "audio",
                        "mimeType": "audio/mpeg",
                    }
                ],
                "part": {"tool": "read_media", "input": {"path": "clip.mp3"}},
            }
        )
    ]
    results = list(parser.parse(_make_lines(lines)))
    assert any(r.type == "tool_result" for r in results), (
        f"Expected tool_result, got: {[r.type for r in results]}"
    )
    assert not any(r.type == "error" for r in results)
    tool_results = [r for r in results if r.type == "tool_result"]
    assert any("[audio: ralph://media/aud-001]" in r.content for r in tool_results), (
        f"Expected '[audio: ralph://media/aud-001]' placeholder, "
        f"got: {[r.content for r in tool_results]}"
    )
