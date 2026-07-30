"""Tests for ClaudeParser noise-suppression improvements."""

from __future__ import annotations

import json

from ralph.agents.parsers.claude import ClaudeParser


def test_thinking_content_block_start_does_not_emit_error() -> None:
    """thinking block_start must not produce an 'unsupported content block type' error."""
    parser = ClaudeParser()
    lines = [
        '{"type":"message_start","message":{"id":"msg-think-1"}}',
        '{"type":"content_block_start","index":0,"content_block":{"type":"thinking"}}',
        '{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"hi"}}',
        '{"type":"content_block_stop","index":0}',
        '{"type":"message_stop"}',
    ]
    results = list(parser.parse(iter(lines)))
    errors = [r for r in results if r.type == "error"]
    assert errors == [], f"Unexpected error results: {errors}"
    thinking = [r for r in results if r.type == "thinking"]
    assert len(thinking) == 1
    assert thinking[0].content == "hi"


def test_claude_model_qualified_lifecycle_is_suppressed() -> None:
    """model-qualified lifecycle markers must be completely suppressed."""
    parser = ClaudeParser()
    lines = [
        "claude/sonnet: message_delta",
        "claude/sonnet: user",
        "claude/sonnet: system (status=requesting)",
        "claude/sonnet: thinking",
    ]
    results = list(parser.parse(iter(lines)))
    assert results == [], f"Expected empty list, got: {results}"


def test_claude_model_qualified_text_and_tool_are_parsed() -> None:
    """model-qualified text lines and tool lines must be parsed correctly."""
    parser = ClaudeParser()
    lines = [
        "claude/sonnet: hello world",
        "claude/sonnet tool: mcp__ralph__read_file (path=ralph-workflow/ralph/x.py)",
    ]
    results = list(parser.parse(iter(lines)))

    text_results = [r for r in results if r.type == "text"]
    assert len(text_results) == 1
    assert text_results[0].content == "hello world"

    tool_results = [r for r in results if r.type == "tool_use"]
    assert len(tool_results) == 1
    assert tool_results[0].content == "mcp__ralph__read_file"
    assert tool_results[0].metadata is not None
    input_obj = tool_results[0].metadata.get("input")
    assert isinstance(input_obj, dict)
    args = input_obj.get("args", "")
    assert "path=ralph-workflow/ralph/x.py" in str(args)


def test_bare_claude_lifecycle_is_suppressed() -> None:
    """Bare (non-model-qualified) lifecycle markers must also be suppressed."""
    parser = ClaudeParser()
    lines = [
        "claude: message_delta",
        "claude: user",
        "claude: thinking",
    ]
    results = list(parser.parse(iter(lines)))
    assert results == [], f"Expected empty list for bare lifecycle, got: {results}"


def test_claude_error_prefix_emits_error() -> None:
    """claude/<model> ✗: error text must surface as type='error'."""
    parser = ClaudeParser()
    lines = ["claude/sonnet ✗: unsupported content block type 'thinking' in agent output"]
    results = list(parser.parse(iter(lines)))
    assert len(results) == 1
    assert results[0].type == "error"
    assert "unsupported" in results[0].content


def test_claude_assistant_message_with_thinking_block_does_not_emit_error() -> None:
    """Assistant event with message.content thinking block must not produce an error."""

    parser = ClaudeParser()
    line = json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "thinking", "thinking": "weighing options"}]},
        }
    )
    results = list(parser.parse(iter([line])))
    errors = [r for r in results if r.type == "error"]
    assert errors == [], f"Unexpected error results: {errors}"
    thinking = [r for r in results if r.type == "thinking"]
    assert len(thinking) == 1
    assert thinking[0].content == "weighing options"


def test_whitespace_only_thinking_delta_is_suppressed() -> None:
    """thinking_delta with whitespace-only content must produce zero thinking lines."""
    parser = ClaudeParser()
    ws_delta = (
        '{"type":"content_block_delta","index":0,'
        '"delta":{"type":"thinking_delta","thinking":"   "}}'
    )
    lines = [
        '{"type":"message_start","message":{"id":"msg-ws-1"}}',
        '{"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}',
        ws_delta,
        '{"type":"content_block_stop","index":0}',
        '{"type":"message_stop"}',
    ]
    results = list(parser.parse(iter(lines)))
    thinking = [r for r in results if r.type == "thinking"]
    assert thinking == [], f"Expected no thinking output for whitespace delta, got: {thinking}"


def test_empty_thinking_delta_is_suppressed() -> None:
    """thinking_delta with empty string must produce zero thinking lines."""
    parser = ClaudeParser()
    lines = [
        '{"type":"message_start","message":{"id":"msg-empty-1"}}',
        '{"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}',
        '{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":""}}',
        '{"type":"content_block_stop","index":0}',
        '{"type":"message_stop"}',
    ]
    results = list(parser.parse(iter(lines)))
    thinking = [r for r in results if r.type == "thinking"]
    assert thinking == [], f"Expected no thinking output for empty delta, got: {thinking}"


def test_non_empty_thinking_delta_is_still_emitted() -> None:
    """Real thinking content must still be emitted after the whitespace guard."""
    parser = ClaudeParser()
    real_delta = (
        '{"type":"content_block_delta","index":0,'
        '"delta":{"type":"thinking_delta","thinking":"deep reasoning"}}'
    )
    lines = [
        '{"type":"message_start","message":{"id":"msg-real-1"}}',
        '{"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}',
        real_delta,
        '{"type":"content_block_stop","index":0}',
        '{"type":"message_stop"}',
    ]
    results = list(parser.parse(iter(lines)))
    thinking = [r for r in results if r.type == "thinking"]
    assert len(thinking) == 1, f"Expected one thinking result, got: {thinking}"
    assert thinking[0].content == "deep reasoning"


def test_headless_system_plumbing_subtypes_are_suppressed() -> None:
    """Top-level ``system`` events that carry no agent-authored content must
    not surface as bare, content-less lines. ``claude -p --output-format
    =stream-json`` emits ``init``/``hook_started``/``hook_response``/
    ``thinking_tokens`` system events dozens of times per turn; each one
    previously fell through to the generic dispatch fallback and emitted an
    empty ``type="system"`` line, flooding operator-visible output with
    blank noise while burying the real semantic content.
    """
    parser = ClaudeParser()
    lines = [
        json.dumps({"type": "system", "subtype": "init", "session_id": "abc"}),
        json.dumps({"type": "system", "subtype": "hook_started", "hook_id": "h1"}),
        json.dumps({"type": "system", "subtype": "hook_response", "output": "huge payload"}),
        json.dumps({"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 4}),
    ]
    results = list(parser.parse(iter(lines)))
    assert results == [], f"Expected pure plumbing system events suppressed, got: {results}"


def test_headless_system_compact_boundary_surfaces_with_content() -> None:
    """A ``system`` subtype outside the known-noise set must still surface,
    and must carry the subtype as content rather than an empty line, so an
    operator can see it happened without it being dropped or blank.
    """
    parser = ClaudeParser()
    line = json.dumps({"type": "system", "subtype": "compact_boundary"})
    results = list(parser.parse(iter([line])))
    assert len(results) == 1
    assert results[0].type == "system"
    assert results[0].content == "compact_boundary"


def test_headless_system_status_surfaces_status_value_not_bare_subtype() -> None:
    """The real ``system/status`` event (observed live: fires once per turn
    boundary) must surface its actual ``status`` value, not just the
    literal word "status", so the content is informative."""
    parser = ClaudeParser()
    line = json.dumps({"type": "system", "subtype": "status", "status": "requesting"})
    results = list(parser.parse(iter([line])))
    assert len(results) == 1
    assert results[0].type == "system"
    assert results[0].content == "status (requesting)"


def test_headless_rate_limit_event_allowed_is_suppressed() -> None:
    """A rate-limit event reporting the account is comfortably within quota
    is pure per-turn telemetry and must be suppressed."""
    parser = ClaudeParser()
    line = json.dumps(
        {
            "type": "rate_limit_event",
            "rate_limit_info": {"status": "allowed", "isUsingOverage": False},
        }
    )
    results = list(parser.parse(iter([line])))
    assert results == [], f"Expected allowed/non-overage rate_limit_event suppressed: {results}"


def test_headless_rate_limit_event_overage_surfaces() -> None:
    """Drawing on overage is operator-relevant even when nominally 'allowed'."""
    parser = ClaudeParser()
    line = json.dumps(
        {
            "type": "rate_limit_event",
            "rate_limit_info": {"status": "allowed", "isUsingOverage": True},
        }
    )
    results = list(parser.parse(iter([line])))
    assert len(results) == 1
    assert results[0].type == "rate_limit_event"
    assert results[0].content == "allowed"


def test_headless_rate_limit_event_non_allowed_status_surfaces() -> None:
    """Any status other than the known-OK set must surface with that status
    as content, including a future/unrecognized status value."""
    parser = ClaudeParser()
    line = json.dumps(
        {
            "type": "rate_limit_event",
            "rate_limit_info": {"status": "rejected", "isUsingOverage": False},
        }
    )
    results = list(parser.parse(iter([line])))
    assert len(results) == 1
    assert results[0].content == "rejected"


def test_headless_rate_limit_event_missing_info_does_not_crash() -> None:
    """A malformed/future rate_limit_event with no rate_limit_info dict must
    not raise, and must fail open (surface) rather than silently vanish."""
    parser = ClaudeParser()
    line = json.dumps({"type": "rate_limit_event"})
    results = list(parser.parse(iter([line])))
    assert len(results) == 1
    assert results[0].content == "unknown"


def test_headless_message_delta_expected_stop_reasons_are_suppressed() -> None:
    """The nested stream_event message_delta fires once per turn with a
    stop_reason; the two routine reasons (mid-turn tool dispatch, and a
    normal end of turn) carry no actionable signal and must be
    suppressed."""
    parser = ClaudeParser()
    lines = [
        json.dumps(
            {
                "type": "stream_event",
                "event": {"type": "message_delta", "delta": {"stop_reason": reason}},
            }
        )
        for reason in ("tool_use", "end_turn")
    ]
    results = list(parser.parse(iter(lines)))
    assert results == [], f"Expected routine stop reasons suppressed, got: {results}"


def test_headless_message_delta_unusual_stop_reason_surfaces() -> None:
    """An unusual stop_reason (truncation, refusal, or any future reason
    this parser has not seen yet) must surface -- an operator needs to
    know a turn did not end normally."""
    parser = ClaudeParser()
    line = json.dumps(
        {
            "type": "stream_event",
            "event": {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}},
        }
    )
    results = list(parser.parse(iter([line])))
    assert len(results) == 1
    assert results[0].type == "message_delta"
    assert results[0].content == "max_tokens"


def test_headless_message_delta_missing_stop_reason_is_suppressed() -> None:
    """A message_delta with no stop_reason (a mid-stream usage-only delta)
    carries nothing actionable and must be suppressed."""
    parser = ClaudeParser()
    line = json.dumps(
        {
            "type": "stream_event",
            "event": {"type": "message_delta", "delta": {"stop_sequence": None}},
        }
    )
    results = list(parser.parse(iter([line])))
    assert results == []


def test_headless_top_level_user_tool_result_success_is_captured() -> None:
    """A top-level 'user' event is how claude -p echoes a tool_result back
    after a tool call. Before this fix it was completely unhandled and the
    result content was silently dropped."""
    parser = ClaudeParser()
    line = json.dumps(
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "tool_use_id": "toolu_1",
                        "type": "tool_result",
                        "content": "hello-world",
                        "is_error": False,
                    }
                ],
            },
        }
    )
    results = list(parser.parse(iter([line])))
    assert len(results) == 1
    assert results[0].type == "tool_result"
    assert results[0].content == "hello-world"


def test_headless_top_level_user_tool_result_error_surfaces_as_error() -> None:
    """A failed tool call must surface as type='error' (matching the
    established Cursor/Pi/Generic parser precedent for is_error), not as a
    routine tool_result, so it is a distinguishable break signal."""
    parser = ClaudeParser()
    line = json.dumps(
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "tool_use_id": "toolu_1",
                        "type": "tool_result",
                        "content": "command not found",
                        "is_error": True,
                    }
                ],
            },
        }
    )
    results = list(parser.parse(iter([line])))
    assert len(results) == 1
    assert results[0].type == "error"
    assert results[0].content == "command not found"


def test_headless_unclassified_top_level_event_with_subtype_surfaces_subtype() -> None:
    """A future top-level event type this parser has never seen must still
    surface, self-describing via its subtype, rather than vanishing or
    rendering as a truly blank line."""
    parser = ClaudeParser()
    line = json.dumps({"type": "context_edit", "subtype": "auto_truncate"})
    results = list(parser.parse(iter([line])))
    assert len(results) == 1
    assert results[0].type == "context_edit"
    assert results[0].content == "auto_truncate"


def test_headless_unclassified_top_level_event_without_subtype_does_not_crash() -> None:
    """A future top-level event with no subtype at all must still parse
    without raising and yield exactly one self-describing line."""
    parser = ClaudeParser()
    line = json.dumps({"type": "future_unknown_event", "payload": {"x": 1}})
    results = list(parser.parse(iter([line])))
    assert len(results) == 1
    assert results[0].type == "future_unknown_event"


def test_headless_unclassified_nested_stream_event_does_not_crash() -> None:
    """A future stream_event-nested type this parser has never seen must
    also surface self-describing rather than raising or vanishing."""
    parser = ClaudeParser()
    line = json.dumps(
        {
            "type": "stream_event",
            "event": {"type": "future_stream_kind", "subtype": "beta"},
        }
    )
    results = list(parser.parse(iter([line])))
    assert len(results) == 1
    assert results[0].type == "future_stream_kind"
    assert results[0].content == "beta"


def test_whitespace_only_thinking_in_assistant_message_is_suppressed() -> None:
    """assistant message with whitespace-only thinking block must not produce thinking lines."""

    parser = ClaudeParser()
    line = json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "thinking", "thinking": "   "}]},
        }
    )
    results = list(parser.parse(iter([line])))
    thinking = [r for r in results if r.type == "thinking"]
    assert thinking == [], f"Expected no thinking for whitespace content, got: {thinking}"
