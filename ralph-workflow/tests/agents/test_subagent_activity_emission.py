from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.parsers import (
    AgentOutputLine,
    ClaudeInteractiveParser,
    ClaudeParser,
    CodexParser,
    GeminiParser,
    GenericParser,
    OpenCodeParser,
    PiParser,
    get_parser,
)
from ralph.agents.parsers.agy import AgyParser
from ralph.mcp.server._activity_sink import reset_subagent_sink, set_subagent_sink
from ralph.pipeline.activity_stream import stream_parsed_agent_activity

if TYPE_CHECKING:
    from collections.abc import Callable


_PARSER_TABLE: list[tuple[str, type]] = [
    ("claude", ClaudeParser),
    ("opencode", OpenCodeParser),
    ("codex", CodexParser),
    ("gemini", GeminiParser),
    ("pi", PiParser),
    ("agy", AgyParser),
    ("generic", GenericParser),
    ("claude_interactive", ClaudeInteractiveParser),
]


def _tool_use_line(tool_name: str) -> AgentOutputLine:
    return AgentOutputLine(
        type="tool_use",
        content=tool_name,
        raw="",
        metadata={"tool": tool_name},
    )


def _tool_result_line(tool_name: str) -> AgentOutputLine:
    return AgentOutputLine(
        type="tool_result",
        content="ok",
        raw="",
        metadata={"tool": tool_name},
    )


def _text_line(content: str) -> AgentOutputLine:
    return AgentOutputLine(type="text", content=content, raw="", metadata={})


def _thinking_line(content: str) -> AgentOutputLine:
    return AgentOutputLine(type="thinking", content=content, raw="", metadata={})


def _error_line(message: str) -> AgentOutputLine:
    return AgentOutputLine(type="error", content=message, raw="", metadata={})


def _raw_line(content: str) -> AgentOutputLine:
    return AgentOutputLine(type="raw", content=content, raw=content, metadata={})


def _stop_line() -> AgentOutputLine:
    return AgentOutputLine(type="stop", raw="", metadata={})


def _unknown_line() -> AgentOutputLine:
    return AgentOutputLine(type="unknown", raw="", metadata={})


def _lifecycle_line(event_type: str = "message_start") -> AgentOutputLine:
    return AgentOutputLine(type=event_type, raw="", metadata={"event": event_type})


def _sink() -> tuple[list[str], Callable[[str], None]]:
    captured: list[str] = []

    def _record(line: str) -> None:
        captured.append(line)

    return captured, _record


def test_parser_factory_returns_parser_with_emit_subagent_activity() -> None:
    for name, _expected_cls in _PARSER_TABLE:
        parser = get_parser(name)
        assert hasattr(parser, "emit_subagent_activity"), (
            f"{name}: parser {type(parser).__name__} missing emit_subagent_activity hook"
        )
        assert callable(parser.emit_subagent_activity), (
            f"{name}: parser.emit_subagent_activity is not callable"
        )


def test_tool_use_line_invokes_sink_with_sanitized_name() -> None:
    for name, _expected_cls in _PARSER_TABLE:
        parser = get_parser(name)
        captured, sink = _sink()
        parser.emit_subagent_activity(_tool_use_line("Bash"), sink)
        assert captured == ["tool_use:Bash"], f"{name}: expected ['tool_use:Bash'], got {captured}"


def test_tool_result_line_invokes_sink_with_sanitized_name() -> None:
    for name, _expected_cls in _PARSER_TABLE:
        parser = get_parser(name)
        captured, sink = _sink()
        parser.emit_subagent_activity(_tool_result_line("Read"), sink)
        assert captured == ["tool_result:Read"], (
            f"{name}: expected ['tool_result:Read'], got {captured}"
        )


def test_text_line_invokes_sink_with_first_eighty_chars() -> None:
    long = "a" * 200
    for name, _expected_cls in _PARSER_TABLE:
        parser = get_parser(name)
        captured, sink = _sink()
        parser.emit_subagent_activity(_text_line(long), sink)
        assert len(captured) == 1, f"{name}: expected one sink call, got {captured}"
        assert captured[0].startswith("text:"), (
            f"{name}: expected summary starting with 'text:', got {captured[0]!r}"
        )
        assert captured[0] == "text:" + "a" * 80, (
            f"{name}: expected exactly 80 'a' chars after the prefix, got {captured[0]!r}"
        )


def test_thinking_line_invokes_sink_with_first_eighty_chars() -> None:
    long = "z" * 200
    for name, _expected_cls in _PARSER_TABLE:
        parser = get_parser(name)
        captured, sink = _sink()
        parser.emit_subagent_activity(_thinking_line(long), sink)
        assert captured == ["thinking:" + "z" * 80], (
            f"{name}: expected thinking summary with 80 chars, got {captured}"
        )


def test_error_line_does_not_invoke_sink() -> None:
    for name, _expected_cls in _PARSER_TABLE:
        parser = get_parser(name)
        captured, sink = _sink()
        parser.emit_subagent_activity(_error_line("boom"), sink)
        assert captured == [], f"{name}: sink MUST NOT be called for error lines, got {captured}"


def test_raw_line_does_not_invoke_sink() -> None:
    for name, _expected_cls in _PARSER_TABLE:
        parser = get_parser(name)
        captured, sink = _sink()
        parser.emit_subagent_activity(_raw_line("random"), sink)
        assert captured == [], f"{name}: sink MUST NOT be called for raw lines, got {captured}"


def test_stop_line_does_not_invoke_sink() -> None:
    for name, _expected_cls in _PARSER_TABLE:
        parser = get_parser(name)
        captured, sink = _sink()
        parser.emit_subagent_activity(_stop_line(), sink)
        assert captured == [], f"{name}: sink MUST NOT be called for stop lines, got {captured}"


def test_unknown_line_does_not_invoke_sink() -> None:
    for name, _expected_cls in _PARSER_TABLE:
        parser = get_parser(name)
        captured, sink = _sink()
        parser.emit_subagent_activity(_unknown_line(), sink)
        assert captured == [], f"{name}: sink MUST NOT be called for unknown lines, got {captured}"


def test_lifecycle_line_does_not_invoke_sink() -> None:
    for name, _expected_cls in _PARSER_TABLE:
        parser = get_parser(name)
        captured, sink = _sink()
        parser.emit_subagent_activity(_lifecycle_line("message_start"), sink)
        assert captured == [], (
            f"{name}: sink MUST NOT be called for lifecycle lines, got {captured}"
        )


def test_tool_use_sanitizes_sensitive_marker_in_tool_name() -> None:
    parser = get_parser("claude")
    captured, sink = _sink()
    parser.emit_subagent_activity(
        AgentOutputLine(
            type="tool_use",
            content="Bash",
            raw="",
            metadata={
                "tool": "Bash",
                "input": {"args": {"command": "rm -rf /etc/passwd", "token": "abc"}},
            },
        ),
        sink,
    )
    assert len(captured) == 1
    assert captured[0].startswith("tool_use:"), captured[0]
    assert "passwd" not in captured[0]
    assert "abc" not in captured[0]


def test_text_line_sanitizes_malformed_args_payload() -> None:
    parser = get_parser("claude")
    captured, sink = _sink()
    parser.emit_subagent_activity(
        AgentOutputLine(
            type="text",
            content='{"args":"secret"tail"}',
            raw="",
            metadata={},
        ),
        sink,
    )
    assert len(captured) == 1
    assert "secret" not in captured[0], (
        f"malformed args value must be redacted, got: {captured[0]!r}"
    )
    assert "tail" not in captured[0], (
        f"malformed args suffix must be redacted, got: {captured[0]!r}"
    )
    assert "<redacted>" in captured[0]


def test_sink_exception_is_swallowed() -> None:

    def bad_sink(_line: str) -> None:
        raise RuntimeError("buggy sink")

    for name, _expected_cls in _PARSER_TABLE:
        parser = get_parser(name)
        try:
            parser.emit_subagent_activity(_tool_use_line("Bash"), bad_sink)
        except Exception as exc:
            msg = f"{name}: emit_subagent_activity leaked a sink exception: {exc}"
            raise AssertionError(msg) from exc


def test_tool_use_without_tool_name_falls_back_to_unknown() -> None:
    parser = get_parser("claude")
    captured, sink = _sink()
    parser.emit_subagent_activity(
        AgentOutputLine(type="tool_use", content="", raw="", metadata={}),
        sink,
    )
    assert captured == ["tool_use:unknown"], f"expected 'tool_use:unknown', got {captured}"


def test_tool_result_without_tool_name_falls_back_to_unknown() -> None:
    parser = get_parser("claude")
    captured, sink = _sink()
    parser.emit_subagent_activity(
        AgentOutputLine(type="tool_result", content="secret result", raw="", metadata={}),
        sink,
    )
    assert captured == ["tool_result:unknown"], f"expected 'tool_result:unknown', got {captured}"


def test_pi_tool_execution_end_uses_documented_tool_name_for_summary() -> None:
    captured: list[str] = []

    def _capture(line: str) -> None:
        captured.append(line)

    token = set_subagent_sink(_capture)
    try:
        stream_parsed_agent_activity(
            [
                json.dumps(
                    {
                        "type": "tool_execution_end",
                        "toolCallId": "call_1",
                        "toolName": "list_allowed_roots",
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": '{"allowed_roots":["/tmp/project"]}',
                                }
                            ]
                        },
                        "isError": False,
                    }
                )
            ],
            parser_type="pi",
            agent_name="pi/test",
            display=None,
        )
    finally:
        reset_subagent_sink(token)

    assert captured == ["tool_result:list_allowed_roots"], (
        f"expected Pi toolName in tool_result summary, got {captured}"
    )


def test_claude_interactive_tool_result_does_not_leak_content() -> None:
    parser = get_parser("claude_interactive")
    captured, sink = _sink()
    payload = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Bash"},
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": [{"type": "text", "text": "secret output"}],
                    },
                ]
            },
        }
    )
    lines = list(parser.parse(iter([payload])))
    tool_result_line = next(line for line in lines if line.type == "tool_result")
    parser.emit_subagent_activity(tool_result_line, sink)
    assert captured == ["tool_result:Bash"], f"expected 'tool_result:Bash', got {captured}"


_TRANSPORT_SAMPLES: list[tuple[str, str, list[str], str]] = [
    (
        "claude",
        "claude/sonnet",
        [
            json.dumps(
                {
                    "type": "content_block_start",
                    "content_block": {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": "ls"},
                    },
                }
            )
        ],
        "tool_use:",
    ),
    (
        "opencode",
        "opencode/test",
        [json.dumps({"type": "tool_use", "part": {"tool": "Bash"}})],
        "tool_use:",
    ),
    (
        "codex",
        "codex/test",
        [
            json.dumps(
                {
                    "type": "item.started",
                    "item": {"type": "mcp_tool_call", "tool": "Bash"},
                }
            )
        ],
        "tool_use:",
    ),
    (
        "gemini",
        "gemini/test",
        [json.dumps({"type": "tool_call", "name": "Bash"})],
        "tool_use:",
    ),
    (
        "pi",
        "pi/test",
        [
            json.dumps(
                {
                    "type": "tool_execution_start",
                    "toolCallId": "call_1",
                    "toolName": "bash",
                    "args": {"command": "ls -la"},
                }
            )
        ],
        "tool_use:",
    ),
    (
        "agy",
        "agy/test",
        ["hello from agy subagent"],
        "text:",
    ),
    (
        "generic",
        "generic/test",
        ["[plain] tool: Bash"],
        "tool_use:",
    ),
    (
        "claude_interactive",
        "claude_interactive/test",
        [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "tool_use", "name": "Bash"}]},
                }
            )
        ],
        "tool_use:",
    ),
]


def test_stream_parsed_agent_activity_invokes_subagent_sink_per_transport() -> None:
    for parser_type, agent_name, lines, expected_prefix in _TRANSPORT_SAMPLES:
        captured: list[str] = []

        def _capture(line: str, _buf: list[str] = captured) -> None:
            _buf.append(line)

        token = set_subagent_sink(_capture)
        try:
            stream_parsed_agent_activity(
                lines,
                parser_type=parser_type,
                agent_name=agent_name,
                display=None,
            )
        finally:
            reset_subagent_sink(token)

        matching = [line for line in captured if line.startswith(expected_prefix)]
        assert len(matching) >= 1, (
            f"{parser_type}: expected at least one subagent sink summary"
            f" starting with {expected_prefix!r}; captured={captured}"
        )


def test_stream_parsed_agent_activity_redacts_authorization_bearer() -> None:
    captured: list[str] = []

    def _capture(line: str) -> None:
        captured.append(line)

    token = set_subagent_sink(_capture)
    try:
        stream_parsed_agent_activity(
            [json.dumps({"type": "text", "content": "Authorization: Bearer SECRET"})],
            parser_type="generic",
            agent_name="generic/test",
            display=None,
        )
    finally:
        reset_subagent_sink(token)

    assert len(captured) >= 1, f"expected a sanitized summary; captured={captured}"
    for line in captured:
        assert "SECRET" not in line, f"bearer token leaked into subagent summary: {line!r}"
    assert any("<redacted>" in line for line in captured), (
        f"expected <redacted> placeholder in sanitized summary; captured={captured}"
    )


def test_text_line_sanitizes_mixed_case_prompt_key() -> None:
    parser = get_parser("claude")
    captured, sink = _sink()
    parser.emit_subagent_activity(
        AgentOutputLine(
            type="text",
            content='{"Prompt": "SECRET-upper"}',
            raw="",
            metadata={},
        ),
        sink,
    )
    assert len(captured) == 1
    assert "SECRET-upper" not in captured[0], (
        f"mixed-case 'Prompt' value must be redacted, got: {captured[0]!r}"
    )
    assert "<redacted>" in captured[0]


def test_text_line_sanitizes_mixed_case_arguments_object() -> None:
    parser = get_parser("claude")
    captured, sink = _sink()
    parser.emit_subagent_activity(
        AgentOutputLine(
            type="text",
            content='{"Arguments": {"token": "SECRET-mixed"}}',
            raw="",
            metadata={},
        ),
        sink,
    )
    assert len(captured) == 1
    assert "SECRET-mixed" not in captured[0], (
        f"mixed-case 'Arguments' value must be redacted, got: {captured[0]!r}"
    )
    assert "token" not in captured[0], f"nested sibling 'token' must NOT leak, got: {captured[0]!r}"
    assert "<redacted>" in captured[0]


def test_text_line_sanitizes_mixed_case_input_key() -> None:
    parser = get_parser("claude")
    captured, sink = _sink()
    parser.emit_subagent_activity(
        AgentOutputLine(
            type="text",
            content='{"Input": "SECRET-input"}',
            raw="",
            metadata={},
        ),
        sink,
    )
    assert len(captured) == 1
    assert "SECRET-input" not in captured[0], (
        f"mixed-case 'Input' value must be redacted, got: {captured[0]!r}"
    )
    assert "<redacted>" in captured[0]


def test_text_line_sanitizes_mixed_case_content_key() -> None:
    parser = get_parser("claude")
    captured, sink = _sink()
    parser.emit_subagent_activity(
        AgentOutputLine(
            type="text",
            content='{"Content": "SECRET-content"}',
            raw="",
            metadata={},
        ),
        sink,
    )
    assert len(captured) == 1
    assert "SECRET-content" not in captured[0], (
        f"mixed-case 'Content' value must be redacted, got: {captured[0]!r}"
    )
    assert "<redacted>" in captured[0]


def test_text_line_sanitizes_malformed_mixed_case_arguments() -> None:
    parser = get_parser("claude")
    captured, sink = _sink()
    parser.emit_subagent_activity(
        AgentOutputLine(
            type="text",
            content='{"Arguments": "secret"tail"}',
            raw="",
            metadata={},
        ),
        sink,
    )
    assert len(captured) == 1
    assert "secret" not in captured[0], (
        f"malformed mixed-case 'Arguments' prefix must be redacted, got: {captured[0]!r}"
    )
    assert "tail" not in captured[0], (
        f"malformed mixed-case 'Arguments' suffix must be redacted, got: {captured[0]!r}"
    )
    assert "<redacted>" in captured[0]
