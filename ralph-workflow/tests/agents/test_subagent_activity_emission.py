"""Per-parser subagent activity emission tests.

Every NDJSON parser (Claude, OpenCode, Codex, Gemini, Pi, Agy, Generic) and
the standalone ClaudeInteractiveParser must expose a public
``emit_subagent_activity(line, sink)`` hook that sanitizes the parsed line and
invokes ``sink`` with a one-line summary so the idle watchdog's subagent sink
gets fresh evidence for agents dispatched mid-start.

The hook is shared (via ParserTemplateBase) for the seven NDJSON parsers and
implemented standalone on ClaudeInteractiveParser (which does not inherit
from ParserTemplateBase).

All tests use the plain ``get_parser(parser_name)`` factory; no subprocess,
no real sleep, no FakeClock - the parser is a deterministic pure-function
class and the test exercises it directly.

Acceptance (per the plan, step 2):
  * Each parser instance exposes ``emit_subagent_activity(line, sink)``.
  * Calling the hook with a ``tool_use`` AgentOutputLine invokes ``sink``
    once with a sanitized ``tool_use:<name>`` string.
  * ``tool_result`` lines invoke ``sink`` with ``tool_result:<name>``.
  * ``text`` lines invoke ``sink`` with ``text:<first-80-chars>``.
  * ``thinking`` lines invoke ``sink`` with ``thinking:<first-80-chars>``.
  * ``error``, ``raw``, ``stop``, ``unknown``, and lifecycle lines do NOT
    invoke the sink.
  * A buggy sink (raises) does NOT propagate the exception.
"""

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


# Parser name -> parser class expected from get_parser(name).
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


# ---------------------------------------------------------------------------
# (1) All 8 parsers expose emit_subagent_activity and emit a sanitized
#     tool_use:<name> summary for a tool_use AgentOutputLine.
# ---------------------------------------------------------------------------


def test_parser_factory_returns_parser_with_emit_subagent_activity() -> None:
    """Every parser exposed by get_parser(name) must expose the hook.

    This is the single API contract the activity-stream integration
    depends on; if any parser regresses and forgets to inherit the hook,
    the parser yields no subagent progress to the watchdog and the
    deferral gate cannot fire.
    """
    for name, _expected_cls in _PARSER_TABLE:
        parser = get_parser(name)
        assert hasattr(parser, "emit_subagent_activity"), (
            f"{name}: parser {type(parser).__name__} missing emit_subagent_activity hook"
        )
        assert callable(parser.emit_subagent_activity), (
            f"{name}: parser.emit_subagent_activity is not callable"
        )


def test_tool_use_line_invokes_sink_with_sanitized_name() -> None:
    """Each parser sanitizes a tool_use line to 'tool_use:<name>'."""
    for name, _expected_cls in _PARSER_TABLE:
        parser = get_parser(name)
        captured, sink = _sink()
        parser.emit_subagent_activity(_tool_use_line("Bash"), sink)
        assert captured == ["tool_use:Bash"], f"{name}: expected ['tool_use:Bash'], got {captured}"


def test_tool_result_line_invokes_sink_with_sanitized_name() -> None:
    """Each parser sanitizes a tool_result line to 'tool_result:<name>'."""
    for name, _expected_cls in _PARSER_TABLE:
        parser = get_parser(name)
        captured, sink = _sink()
        parser.emit_subagent_activity(_tool_result_line("Read"), sink)
        assert captured == ["tool_result:Read"], (
            f"{name}: expected ['tool_result:Read'], got {captured}"
        )


def test_text_line_invokes_sink_with_first_eighty_chars() -> None:
    """text lines produce 'text:<first-80-chars-stripped>'."""
    long = "a" * 200
    for name, _expected_cls in _PARSER_TABLE:
        parser = get_parser(name)
        captured, sink = _sink()
        parser.emit_subagent_activity(_text_line(long), sink)
        # The first 80 chars of the line, stripped; the parser layer may
        # apply the watchdog's 200-char cap AFTER the type prefix, so the
        # emitted summary is exactly ``text:aaaa...`` (80 a's).
        assert len(captured) == 1, f"{name}: expected one sink call, got {captured}"
        assert captured[0].startswith("text:"), (
            f"{name}: expected summary starting with 'text:', got {captured[0]!r}"
        )
        assert captured[0] == "text:" + "a" * 80, (
            f"{name}: expected exactly 80 'a' chars after the prefix, got {captured[0]!r}"
        )


def test_thinking_line_invokes_sink_with_first_eighty_chars() -> None:
    """thinking lines produce 'thinking:<first-80-chars-stripped>'."""
    long = "z" * 200
    for name, _expected_cls in _PARSER_TABLE:
        parser = get_parser(name)
        captured, sink = _sink()
        parser.emit_subagent_activity(_thinking_line(long), sink)
        assert captured == ["thinking:" + "z" * 80], (
            f"{name}: expected thinking summary with 80 chars, got {captured}"
        )


# ---------------------------------------------------------------------------
# (2) Non-emittable lines (error / raw / stop / unknown / lifecycle) MUST
#     NOT invoke the sink.
# ---------------------------------------------------------------------------


def test_error_line_does_not_invoke_sink() -> None:
    """error lines are operator-visible on the display path; the sink
    is reserved for 'demonstrable work' (tool_use, tool_result, text,
    thinking). Error lines MUST NOT count as subagent progress so a
    hard-failing child does not keep the watchdog deferring forever.
    """
    for name, _expected_cls in _PARSER_TABLE:
        parser = get_parser(name)
        captured, sink = _sink()
        parser.emit_subagent_activity(_error_line("boom"), sink)
        assert captured == [], f"{name}: sink MUST NOT be called for error lines, got {captured}"


def test_raw_line_does_not_invoke_sink() -> None:
    """raw lines are unparsed fallback content; never emit to the sink."""
    for name, _expected_cls in _PARSER_TABLE:
        parser = get_parser(name)
        captured, sink = _sink()
        parser.emit_subagent_activity(_raw_line("random"), sink)
        assert captured == [], f"{name}: sink MUST NOT be called for raw lines, got {captured}"


def test_stop_line_does_not_invoke_sink() -> None:
    """stop markers are end-of-stream; never emit to the sink."""
    for name, _expected_cls in _PARSER_TABLE:
        parser = get_parser(name)
        captured, sink = _sink()
        parser.emit_subagent_activity(_stop_line(), sink)
        assert captured == [], f"{name}: sink MUST NOT be called for stop lines, got {captured}"


def test_unknown_line_does_not_invoke_sink() -> None:
    """unknown lines (no recognized type) MUST NOT emit to the sink."""
    for name, _expected_cls in _PARSER_TABLE:
        parser = get_parser(name)
        captured, sink = _sink()
        parser.emit_subagent_activity(_unknown_line(), sink)
        assert captured == [], f"{name}: sink MUST NOT be called for unknown lines, got {captured}"


def test_lifecycle_line_does_not_invoke_sink() -> None:
    """Lifecycle event kinds (message_start, message_stop, etc.) MUST
    NOT invoke the sink. Only the four demonstrable-work kinds
    (tool_use, tool_result, text, thinking) emit to the sink.
    """
    for name, _expected_cls in _PARSER_TABLE:
        parser = get_parser(name)
        captured, sink = _sink()
        parser.emit_subagent_activity(_lifecycle_line("message_start"), sink)
        assert captured == [], (
            f"{name}: sink MUST NOT be called for lifecycle lines, got {captured}"
        )


# ---------------------------------------------------------------------------
# (3) Sanitization: sensitive content in tool names MUST be redacted.
# ---------------------------------------------------------------------------


def test_tool_use_sanitizes_sensitive_marker_in_tool_name() -> None:
    """Tool names that contain a sensitive marker MUST be sanitized so
    the operator-visible subagent_activity string never echoes raw
    tool arguments / file paths / bearer tokens.

    The sanitizer strips JSON-fragment values, sensitive path roots,
    and bearer tokens (mirroring the watchdog's
    ``_sanitize_subagent_description`` semantics so the parser layer
    and the watchdog layer stay in sync).
    """
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
    # The summary MUST start with tool_use:Bash and MUST NOT echo the
    # raw ``arguments`` value or the token.
    assert captured[0].startswith("tool_use:"), captured[0]
    assert "passwd" not in captured[0]
    assert "abc" not in captured[0]


def test_text_line_sanitizes_malformed_args_payload() -> None:
    """A text line containing malformed JSON with an ``args`` value MUST
    have the entire value redacted.

    This is the analysis-feedback regression: the parser-layer
    fallback regex previously only listed ``arguments`` and left
    ``args`` payloads (e.g. ``text:{"args":"secret"tail"}``) visible.
    """
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


# ---------------------------------------------------------------------------
# (4) A buggy sink (raises) MUST NOT propagate the exception. The hook is
#     called from inside stream_parsed_agent_activity which is itself a
#     fan-out path; a buggy sink cannot crash the activity stream.
# ---------------------------------------------------------------------------


def test_sink_exception_is_swallowed() -> None:
    """A sink that raises MUST NOT propagate the exception.

    Mirrors the existing OpenCodeExecutionStrategy ``subagent_activity_sink``
    exception-swallow contract: a buggy sink cannot corrupt the line loop.
    """

    def bad_sink(_line: str) -> None:
        raise RuntimeError("buggy sink")

    for name, _expected_cls in _PARSER_TABLE:
        parser = get_parser(name)
        # The hook MUST swallow the exception so a buggy sink never
        # crashes the parser layer.
        try:
            parser.emit_subagent_activity(_tool_use_line("Bash"), bad_sink)
        except Exception as exc:  # pragma: no cover - only fires on regression
            msg = f"{name}: emit_subagent_activity leaked a sink exception: {exc}"
            raise AssertionError(msg) from exc


# ---------------------------------------------------------------------------
# (5) Default tool name fallback: a tool_use line WITHOUT a tool_name in
#     metadata must still emit a well-formed summary (using 'unknown').
# ---------------------------------------------------------------------------


def test_tool_use_without_tool_name_falls_back_to_unknown() -> None:
    """A tool_use line WITHOUT a tool_name MUST emit 'tool_use:unknown'.

    Parsers populate metadata inconsistently; the hook MUST handle a
    missing tool name without crashing and MUST use a stable
    placeholder so the operator sees the bare tool_use event.
    """
    parser = get_parser("claude")
    captured, sink = _sink()
    parser.emit_subagent_activity(
        AgentOutputLine(type="tool_use", content="", raw="", metadata={}),
        sink,
    )
    assert captured == ["tool_use:unknown"], f"expected 'tool_use:unknown', got {captured}"


def test_tool_result_without_tool_name_falls_back_to_unknown() -> None:
    """A tool_result line WITHOUT a tool_name MUST emit 'tool_result:unknown'.

    Tool-result lines often carry raw result text in ``line.content``.
    The hook MUST NOT fall back to that content; doing so would leak
    operator-visible subagent output into the watchdog summary.
    """
    parser = get_parser("claude")
    captured, sink = _sink()
    parser.emit_subagent_activity(
        AgentOutputLine(type="tool_result", content="secret result", raw="", metadata={}),
        sink,
    )
    assert captured == ["tool_result:unknown"], f"expected 'tool_result:unknown', got {captured}"


def test_pi_tool_execution_end_uses_documented_tool_name_for_summary() -> None:
    """Pi ``tool_execution_end.toolName`` must name the tool-result summary.

    Pi's JSON event stream documents the field as camelCase ``toolName``.
    Losing that field made successful tool results appear as
    ``tool_result:unknown`` in the subagent progress channel even though the
    raw tool-result payload was present elsewhere in the transcript.
    """
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
    """A real ClaudeInteractiveParser tool_result line must not echo raw content.

    The transcript parser emits ``tool_result`` events with the raw
    result text in ``line.content`` and no metadata.tool by default.
    The parser now threads the last seen tool name into metadata, so
    the summary must be ``tool_result:<name>`` rather than
    ``tool_result:<raw result text>``.
    """
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


# ---------------------------------------------------------------------------
# (6) Per-transport stream_parsed_agent_activity wiring: every supported
#     parser feeds the subagent sink through the contextvar pipeline.
# ---------------------------------------------------------------------------

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
    """Each supported transport forwards a sanitized subagent summary to
    the watchdog sink through ``stream_parsed_agent_activity``.

    The sink is bound via the ``set_subagent_sink`` contextvar; the
    activity stream's internal ``invoke_subagent_sink`` call must reach
    the per-run watchdog's ``record_subagent_work`` surface for every
    parser (Claude, OpenCode, Codex, Gemini, Pi, Agy, Generic,
    ClaudeInteractive).
    """
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
    """A subagent summary containing ``Authorization: Bearer SECRET`` is
    sanitized before it reaches the watchdog sink.

    The parser-layer sanitizer (``_sanitize_parser_subagent_summary``)
    must strip bearer tokens from operator-visible summaries so the
    subagent_activity channel never leaks credentials.
    """
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


# ---------------------------------------------------------------------------
# (7) Mixed-case sensitive-key redaction (analysis-feedback: parser layer
#     must redact ``Prompt`` / ``Arguments`` / ``Input`` / ``Content``
#     exactly like lowercase variants).
# ---------------------------------------------------------------------------


def test_text_line_sanitizes_mixed_case_prompt_key() -> None:
    """A text line containing ``\"Prompt\": \"<secret>\"`` redacts the value."""
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
    """A text line containing ``\"Arguments\": {...}`` redacts the whole object."""
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
    """A text line containing ``\"Input\": \"<secret>\"`` redacts the value."""
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
    """A text line containing ``\"Content\": \"<secret>\"`` redacts the value."""
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
    """A malformed JSON value under ``\"Arguments\"`` is fully redacted.

    The fallback regex is case-insensitive, so mixed-case markers in
    malformed JSON are caught exactly like lowercase markers.
    """
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
