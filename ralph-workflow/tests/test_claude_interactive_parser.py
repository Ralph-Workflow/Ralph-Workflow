from __future__ import annotations

import json

import pytest

from ralph.agents.activity import AgentActivityKind
from ralph.agents.execution_state import ClaudeInteractiveExecutionStrategy
from ralph.agents.parsers.claude_interactive import (
    ClaudeInteractiveParser,
    ClaudeInteractiveTranscriptParser,
)
from ralph.agents.parsers.claude_interactive_transcript_parser import (
    _count_box_drawing,
    _is_tui_chrome,
)
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig
from ralph.display.vt_normalizer import normalize_vt_text
from ralph.pipeline.activity_stream import stream_parsed_agent_activity


def test_vt_normalizer_strips_cursor_noise_but_keeps_semantic_text() -> None:
    raw = "\x1b[?25l\r\x1b[2Kclaude tool: read_file\n\x1b[?25h"

    normalized = normalize_vt_text(raw)

    assert normalized == "claude tool: read_file\n"


def test_interactive_parser_extracts_tool_use_from_tui_transcript() -> None:
    parser = ClaudeInteractiveTranscriptParser()

    events = parser.feed("claude tool: read_file\n")

    assert [event.kind for event in events] == ["tool_use"]
    assert events[0].text == "claude tool: read_file"


def test_interactive_parser_extracts_session_id_from_transcript_line() -> None:
    parser = ClaudeInteractiveTranscriptParser()

    events = parser.feed("Claude session ready. Session ID: pty-session-77\n")

    assert parser.session_id == "pty-session-77"
    assert [event.kind for event in events] == ["session"]


def test_interactive_parser_does_not_misclassify_plain_output_with_tool_token() -> None:
    parser = ClaudeInteractiveTranscriptParser()

    events = parser.feed("Rendered help text includes tool: read_file as an example.\n")

    assert [event.kind for event in events] == ["output"]
    assert events[0].text == "Rendered help text includes tool: read_file as an example."


def test_interactive_parser_suppresses_repeated_json_tool_use_event() -> None:
    parser = ClaudeInteractiveTranscriptParser()
    payload = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "read_file"}],
            },
        }
    )

    first_events = parser.feed(payload)
    second_events = parser.feed(payload)

    assert [event.kind for event in first_events] == ["tool_use"]
    assert second_events == []


def test_claude_interactive_parser_regression_session_id_is_emitted_once() -> None:
    """User-reported Claude smoke parsing regression, 2026-07-14."""
    parser = ClaudeInteractiveTranscriptParser()
    session_id = "pty-session-77"

    first_events = parser.feed(
        json.dumps({"type": "mode", "mode": "normal", "sessionId": session_id})
    )
    second_events = parser.feed(
        json.dumps(
            {
                "type": "assistant",
                "sessionId": session_id,
                "message": {"content": [{"type": "text", "text": "Working now."}]},
            }
        )
    )
    third_events = parser.feed(
        json.dumps(
            {
                "type": "assistant",
                "sessionId": session_id,
                "message": {
                    "content": [{"type": "tool_use", "id": "toolu_read", "name": "read_file"}]
                },
            }
        )
    )

    assert [event.kind for event in first_events] == ["session"]
    assert [event.kind for event in second_events] == ["output"]
    assert [event.kind for event in third_events] == ["tool_use"]


def test_claude_interactive_parser_regression_parallel_results_keep_tool_identity() -> None:
    """User-reported Claude smoke parsing regression, 2026-07-14."""
    parser = ClaudeInteractiveParser()
    lines = iter(
        [
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_read",
                                "name": "mcp__ralph__read_file",
                                "input": {"path": "PROMPT.md"},
                            },
                            {
                                "type": "tool_use",
                                "id": "toolu_stat",
                                "name": "mcp__ralph__stat_path",
                                "input": {"path": "tmp"},
                            },
                        ]
                    },
                }
            ),
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_read",
                                "content": [{"type": "text", "text": "prompt contents"}],
                            }
                        ]
                    },
                }
            ),
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_stat",
                                "content": [{"type": "text", "text": "directory metadata"}],
                            }
                        ]
                    },
                }
            ),
        ]
    )

    results = list(parser.parse(lines))
    tool_uses = [result for result in results if result.type == "tool_use"]
    tool_results = [result for result in results if result.type == "tool_result"]

    assert tool_uses[0].metadata == {
        "tool": "mcp__ralph__read_file",
        "tool_use_id": "toolu_read",
        "input": {"path": "PROMPT.md"},
    }
    assert tool_uses[1].metadata == {
        "tool": "mcp__ralph__stat_path",
        "tool_use_id": "toolu_stat",
        "input": {"path": "tmp"},
    }
    assert [result.metadata["tool"] for result in tool_results] == [
        "mcp__ralph__read_file",
        "mcp__ralph__stat_path",
    ]
    assert [result.metadata["tool_use_id"] for result in tool_results] == [
        "toolu_read",
        "toolu_stat",
    ]
    assert [result.content for result in tool_results] == [
        "prompt contents",
        "directory metadata",
    ]


def test_claude_interactive_activity_stream_regression_renders_tool_result() -> None:
    """User-reported Claude smoke parsing regression, 2026-07-14."""
    config = AgentConfig(
        cmd="claude",
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE_INTERACTIVE,
    )
    lines = [
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_read",
                            "name": "mcp__ralph__read_file",
                            "input": {"path": "PROMPT.md"},
                        }
                    ]
                },
            }
        ),
        json.dumps(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_read",
                            "content": [{"type": "text", "text": "prompt contents"}],
                        }
                    ]
                },
            }
        ),
    ]
    rendered: list[str] = []

    stream_parsed_agent_activity(
        lines,
        parser_type="claude",
        agent_name="claude/haiku",
        agent_config=config,
        rendered_output_sink=rendered,
    )

    assert rendered == [
        "claude/haiku tool: mcp__ralph__read_file (path=PROMPT.md)",
        "claude/haiku result: prompt contents",
    ]


def test_interactive_parser_extracts_tool_result_from_assistant_message_content() -> None:
    parser = ClaudeInteractiveTranscriptParser()
    payload = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_exec_1",
                        "content": [{"type": "text", "text": "Exit code: 0\nStdout:\nhello"}],
                    }
                ],
            },
        }
    )

    events = parser.feed(payload)

    assert [event.kind for event in events] == ["tool_result"]
    assert events[0].text == "claude result: Exit code: 0\nStdout:\nhello"


def test_claude_interactive_strategy_classifies_assistant_tool_result_as_tool_result() -> None:
    strategy = ClaudeInteractiveExecutionStrategy()
    payload = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_exec_1",
                        "content": [{"type": "text", "text": "Exit code: 0\nStdout:\nhello"}],
                    }
                ],
            },
        }
    )

    signal = strategy.classify_activity_line(payload)

    assert signal is not None
    assert signal.kind == AgentActivityKind.TOOL_RESULT
    assert signal.raw == "claude result: Exit code: 0\nStdout:\nhello"


def test_vt_normalizer_collapses_repaint_duplicates_to_single_semantic_line() -> None:
    raw = "\rclaude tool: read_file\rclaude tool: read_file\n"

    normalized = normalize_vt_text(raw)

    assert normalized == "claude tool: read_file\n"


def test_claude_interactive_strategy_classifies_vt_tool_line_as_tool_use() -> None:
    strategy = ClaudeInteractiveExecutionStrategy()

    signal = strategy.classify_activity_line("\x1b[2K\rclaude tool: read_file\n")

    assert signal is not None
    assert signal.kind == AgentActivityKind.TOOL_USE


def test_claude_interactive_strategy_prioritizes_tool_use_over_later_output_from_same_feed() -> (
    None
):
    strategy = ClaudeInteractiveExecutionStrategy()
    payload = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "read_file"},
                    {"type": "text", "text": "Done reading file."},
                ],
            },
        }
    )

    signal = strategy.classify_activity_line(payload)

    assert signal is not None
    assert signal.kind == AgentActivityKind.TOOL_USE
    assert signal.raw == "claude tool: read_file"


def test_claude_interactive_parser_surfaces_subscription_limit_errors() -> None:
    parser = ClaudeInteractiveParser()
    lines = iter(
        [
            json.dumps(
                {
                    "type": "error",
                    "error": {
                        "type": "rate_limit_error",
                        "message": "You've hit your session limit · resets 3:45pm",
                    },
                }
            )
        ]
    )

    results = list(parser.parse(lines))

    assert len(results) == 1
    assert results[0].type == "error"
    assert results[0].content == "You've hit your session limit · resets 3:45pm"


# ---------------------------------------------------------------------------
# Thinking status parsing — Claude Code interactive TUI status display
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_line, expected_kinds",
    [
        ("\r✢Tinkering… (5s · ↓292 tokens)\n", []),
        ("\r✶Actioning… (14s · ↓1.2k tokens)\n", []),
        ("\r✢Hullaballooing… (23s · ↓ 1.9k tokens)\n", []),
        ("\r✶Quaing\n", []),
        ("\r· thinking)\n", []),
        ("\r✢Clauding…\n", []),
        ("\r●Let me try reading the file\n", []),
        ("\r↓292 tokens\n", []),
        ("\r(5s · thinking)\n", []),
        # Normal output text must still be classified as output
        ("\rNow let me explore the repository\n", ["output"]),
        ("\rRendered help text includes tool: read_file as an example.\n", ["output"]),
    ],
)
def test_interactive_parser_classifies_thinking_status_as_thinking(
    raw_line: str, expected_kinds: list[str]
) -> None:
    parser = ClaudeInteractiveTranscriptParser()

    events = parser.feed(raw_line)

    kinds = [event.kind for event in events]
    assert kinds == expected_kinds, f"Expected {expected_kinds}, got {kinds} for {raw_line!r}"


def test_interactive_parser_accumulates_thinking_status_in_thinking_accumulator() -> None:
    parser = ClaudeInteractiveParser()
    lines = iter(
        [
            "\u2722Tinkering\u2026 (5s \u00b7 \u2193292 tokens)\n",
            "Now let me explore the repository.\n",
            "claude tool: read_file\n",
        ]
    )

    results = list(parser.parse(lines))

    text_results = [r for r in results if r.type == "text"]
    thinking_results = [r for r in results if r.type == "thinking"]
    tool_results = [r for r in results if r.type == "tool_use"]

    assert len(text_results) == 1
    assert text_results[0].content == "Now let me explore the repository."
    assert len(thinking_results) == 0
    assert len(tool_results) == 1


def test_thinking_status_does_not_override_output_priority_in_execution_strategy() -> None:
    strategy = ClaudeInteractiveExecutionStrategy()

    thinking_line = "\r✢Tinkering… (5s · ↓292 tokens)\n"
    output_line = "\rHello world\n"

    thinking_signal = strategy.classify_activity_line(thinking_line)
    assert thinking_signal is not None
    assert thinking_signal.kind == AgentActivityKind.OUTPUT_LINE

    output_signal = strategy.classify_activity_line(output_line)
    assert output_signal is not None
    assert output_signal.kind == AgentActivityKind.OUTPUT_LINE


def test_vt_normalization_preserves_thinking_status_structure() -> None:
    raw = "\r✢Tinkering… (5s · ↓292 tokens)\n"

    normalized = normalize_vt_text(raw)

    assert normalized == "✢Tinkering… (5s · ↓292 tokens)\n"


def test_plain_text_contains_thinking_symbol_still_classified_as_output() -> None:
    """Text containing '·' (middle dot) in normal sentences must not be mistaken for thinking."""
    parser = ClaudeInteractiveTranscriptParser()

    events = parser.feed("You've hit your session limit · resets 3:45pm\n")

    assert [event.kind for event in events] == ["output"]


# ---------------------------------------------------------------------------
# TUI chrome detection tests — _is_tui_chrome, _count_box_drawing
# ---------------------------------------------------------------------------


def test_count_box_drawing_returns_zero_for_plain_text() -> None:

    assert _count_box_drawing("hello world") == 0
    assert _count_box_drawing("def foo(): pass") == 0


def test_count_box_drawing_counts_unicode_box_chars() -> None:

    assert _count_box_drawing("\u2500\u2502\u250c") == 3
    assert _count_box_drawing("\u2500text\u2500") == 2


def test_count_box_drawing_counts_block_elements() -> None:

    assert _count_box_drawing("\u2588\u2591\u2593") == 3


def test_is_tui_chrome_pure_box_drawing_border() -> None:

    assert _is_tui_chrome("\u2500" * 50) is True
    assert _is_tui_chrome("\u2502" * 20) is True


def test_is_tui_chrome_claude_code_splash_header() -> None:

    assert (
        _is_tui_chrome("\u256d\u2500\u2500\u2500ClaudeCodev2.1.158" + "\u2500" * 50 + "\u256e")
        is True
    )


def test_is_tui_chrome_spinner_line() -> None:

    assert _is_tui_chrome("\u273dSpinning\u2026 (0s)") is True
    assert _is_tui_chrome("\u273bSping\u2026") is True


def test_is_tui_chrome_token_counter_in_status_bar() -> None:

    assert _is_tui_chrome("0tokens") is True
    assert _is_tui_chrome("1.2k tokens") is True


def test_is_tui_chrome_plugin_status() -> None:

    assert _is_tui_chrome("2 plugins failed to install") is True


def test_is_tui_chrome_model_identity_in_status_bar() -> None:

    assert _is_tui_chrome("Haiku4.5 \u00b7 ClaudeMax \u00b7 ken.li156@gmail.com's") is True


def test_is_tui_chrome_prompt_indicator() -> None:

    assert _is_tui_chrome("\u23f5\u23f5bypass permissions on (shift+tab to cycle)") is True


def test_is_tui_chrome_tui_frame_with_low_content() -> None:

    assert _is_tui_chrome("\u2502 Tips \u2502") is True


def test_is_tui_chrome_does_not_filter_legitimate_output() -> None:

    assert _is_tui_chrome("hello world") is False
    assert _is_tui_chrome("def test_something(): assert True") is False
    assert (
        _is_tui_chrome("You are running in an AUTOMATED PIPELINE with NO human supervision.")
        is False
    )
    assert (
        _is_tui_chrome("- DO NOT ask the user for input, clarification, or confirmation") is False
    )


def test_is_tui_chrome_does_not_filter_json_ndjson_events() -> None:

    assert (
        _is_tui_chrome(
            '{"type":"assistant","message":{"content":[{"type":"text","text":"hello"}]}}'
        )
        is False
    )


def test_is_tui_chrome_agent_output_with_unicode_box_drawing_is_preserved() -> None:
    """Agent output containing Unicode box-drawing chars with substantial content."""

    table_line = "\u2502 Name        \u2502 Status  \u2502 Description                    \u2502"
    assert _is_tui_chrome(table_line) is False


def test_is_tui_chrome_empty_string() -> None:

    assert _is_tui_chrome("") is True


def test_is_tui_chrome_up_arrow_status_bar() -> None:

    assert _is_tui_chrome("\u2b06/gsd-update \u2502Haiku4.5 \u2502TinyTorrent") is True


def test_is_tui_chrome_keyboard_shortcut_hint() -> None:

    assert _is_tui_chrome("shift+tab to cycle") is True
    assert _is_tui_chrome("esc to interrupt") is True


# ---------------------------------------------------------------------------
# Integration: _event_for_text returns None for TUI chrome
# ---------------------------------------------------------------------------


def test_event_for_text_returns_none_for_box_drawing_border() -> None:
    parser = ClaudeInteractiveTranscriptParser()

    event = parser._event_for_text("\u2500" * 50)

    assert event is None


def test_event_for_text_returns_none_for_splash_header() -> None:
    parser = ClaudeInteractiveTranscriptParser()

    event = parser._event_for_text("\u256d\u2500\u2500\u2500ClaudeCodev2.1.158\u256e")

    assert event is None


def test_event_for_text_returns_none_for_spinner() -> None:
    parser = ClaudeInteractiveTranscriptParser()

    event = parser._event_for_text("\u273dSpinning\u2026 (0s)")

    assert event is None


def test_is_tui_chrome_does_not_filter_genuine_thinking_through_event_for_text() -> None:
    parser = ClaudeInteractiveTranscriptParser()

    event = parser._event_for_text("\u25cfLet me try reading the file")

    assert event is None


# ---------------------------------------------------------------------------
# Integration: feed() silently drops TUI lines
# ---------------------------------------------------------------------------


def test_feed_drops_tui_lines_but_keeps_real_output() -> None:
    parser = ClaudeInteractiveTranscriptParser()

    lines = (
        "\u256d\u2500\u2500\u2500ClaudeCodev2.1.158\u2500\u2500\u256e\n"
        "\u2502\u2502Tipsforgetting\u2502\n"
        "\u2502WelcomebackKen!\u2502started\u2502\n"
        "Now let me explore the repository.\n"
        "claude tool: read_file\n"
        "\u273dSpinning\u2026 (0s)\n"
        "0tokens\n"
    )
    events = parser.feed(lines)

    kinds = [event.kind for event in events]
    assert "output" in kinds
    assert "tool_use" in kinds
    output_texts = [e.text for e in events if e.kind == "output"]
    for tui_marker in ("ClaudeCode", "Tipsforgetting", "WelcomebackKen", "Spinning", "tokens"):
        assert not any(tui_marker in t for t in output_texts), (
            f"TUI marker '{tui_marker}' leaked into output: {output_texts}"
        )


def test_feed_preserves_legitimate_agent_text_after_tui_splash() -> None:
    parser = ClaudeInteractiveTranscriptParser()

    lines = (
        "\u2502 Run /init to create a \u2502\n"
        "I will now implement the fix.\n"
        "The following changes are needed:\n"
    )
    events = parser.feed(lines)

    output_texts = [e.text for e in events if e.kind == "output"]
    assert len(output_texts) >= 1
    assert "I will now implement the fix." in output_texts
    assert "The following changes are needed:" in output_texts


# ---------------------------------------------------------------------------
# Oracle-identified boundary tests
# ---------------------------------------------------------------------------


def test_is_tui_chrome_does_not_filter_agent_output_starting_with_up_arrow() -> None:

    assert _is_tui_chrome("\u2191 Increased test coverage from 72% to 85%") is False
    assert _is_tui_chrome("\u2191 Performance improved by 23%") is False


def test_is_tui_chrome_does_not_filter_linux_menu_prefix() -> None:

    assert _is_tui_chrome("\u258c1. Yes, I trust this folder") is False


def test_is_tui_chrome_does_not_filter_genuine_thinking_indicator() -> None:

    assert _is_tui_chrome("\u25cfLet me try reading the file") is False


def test_count_box_drawing_catches_range_chars_not_in_frozenset() -> None:

    assert _count_box_drawing("\u253b") == 1


def test_is_tui_chrome_does_not_filter_genuine_thinking_through_event_for_text_duplicate() -> None:
    parser = ClaudeInteractiveTranscriptParser()

    event = parser._event_for_text("\u25cfLet me try reading the file")

    assert event is None


def test_event_for_text_classifies_genuine_output_with_up_arrow_as_output() -> None:
    parser = ClaudeInteractiveTranscriptParser()

    event = parser._event_for_text("\u2191 Increased test coverage")

    assert event is not None
    assert event.kind == "output"


def test_interactive_parser_classifies_thinking_cycle_as_thinking() -> None:
    parser = ClaudeInteractiveTranscriptParser()

    events = parser.feed("9thinking \u00b710s \u00b7 \u2193 329 tokens \u00b7 thinking)\n")

    kinds = [event.kind for event in events]
    assert kinds == [], f"Expected [thinking], got {kinds}"


def test_event_for_text_classifies_thinking_cycle_as_thinking() -> None:
    parser = ClaudeInteractiveTranscriptParser()

    event = parser._event_for_text("9thinking \u00b710s \u00b7 \u2193 329 tokens \u00b7 thinking)")

    assert event is None


# ---------------------------------------------------------------------------
# Lenient thinking fragment tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fragment",
    [
        "\u00b7thinking",
        "nthinking",
        "htthinking",
        "aithinking",
        "2thinking",
        "9thinking",
        "\u2733g20thinking",
        "\u00b7thinking)",
    ],
)
def test_lenient_thinking_catches_thinking_fragments(fragment: str) -> None:
    parser = ClaudeInteractiveTranscriptParser()

    event = parser._event_for_text(fragment)

    assert event is None, f"Fragment {fragment!r} should be dropped (TUI noise)"


@pytest.mark.parametrize(
    "text",
    [
        "I was thinking about the approach",
        "The thinking behind this design is",
        "2thinking points to consider",
    ],
)
def test_lenient_thinking_does_not_false_positive_on_prose(text: str) -> None:
    parser = ClaudeInteractiveTranscriptParser()
    # Legitimate prose with "thinking" arrives via JSON with mode="output".
    # In idle mode (simulated here), the word-boundary "thinking" check drops it
    # as a precaution. Set mode to "output" to test the real code path.
    parser._current_content_mode = "output"
    event = parser._event_for_text(text)

    assert event is not None, f"Text {text!r} was dropped"
    assert event.kind == "output", f"Text {text!r} classified as {event.kind}, expected output"


# ---------------------------------------------------------------------------
# Oracle-identified gap tests
# ---------------------------------------------------------------------------


def test_lenient_catches_thinking_with_trailing_paren() -> None:
    parser = ClaudeInteractiveTranscriptParser()

    event = parser._event_for_text("2thinking)")

    assert event is None


def test_fragmented_status_line_caught_by_regex() -> None:
    parser = ClaudeInteractiveTranscriptParser()

    event = parser._event_for_text("\u00b7 10s \u00b7 thinking)")

    assert event is None


def test_bare_token_counter_dropped_by_tui_chrome() -> None:
    parser = ClaudeInteractiveTranscriptParser()

    event = parser._event_for_text("1.23k")

    assert event is None


def test_bare_token_counter_variant_dropped() -> None:
    parser = ClaudeInteractiveTranscriptParser()

    event = parser._event_for_text("292k")

    assert event is None


def test_short_prose_with_thinking_is_output() -> None:
    parser = ClaudeInteractiveTranscriptParser()
    # "I am thinking" arrives via JSON with mode="output" in real usage
    parser._current_content_mode = "output"
    event = parser._event_for_text("I am thinking")

    assert event is not None
    assert event.kind == "output"


def test_standalone_spinner_label_with_ellipsis_is_thinking() -> None:
    parser = ClaudeInteractiveTranscriptParser()

    event = parser._event_for_text("Symbioting\u2026 Symbioting\u2026")

    assert event is None


def test_vt_normalizer_strips_charset_designation_and_cursor_save_escapes() -> None:
    # Observed live with Claude Code 2.1.218: ESC(B (G0 charset), SI (\x0f),
    # and DECSC/DECRC (ESC7/ESC8) survived normalization and leaked into
    # parsed agent text.
    raw = "/rc\x1b(B\x0f\x1b(B\x0f\x1b7\x1b8\x1b(B\x0f\x1b7\x1b8\n"

    normalized = normalize_vt_text(raw)

    assert normalized == "/rc\n"


def test_vt_normalizer_strips_shift_out_and_keypad_mode_escapes() -> None:
    raw = "\x0ehello\x0f world\x1b=\x1b>\n"

    normalized = normalize_vt_text(raw)

    assert normalized == "hello world\n"


def test_exit_banner_resume_line_is_chrome_even_in_output_mode() -> None:
    # Claude Code >= 2.1.x prints the exit banner on two lines:
    #   Resume this session with:
    #   claude --resume <id>
    # The bare first line must never be classified as agent output.
    parser = ClaudeInteractiveTranscriptParser()
    parser._current_content_mode = "output"

    event = parser._event_for_text("Resume this session with:")

    assert event is None


def test_exit_banner_resume_id_line_still_yields_session() -> None:
    parser = ClaudeInteractiveTranscriptParser()
    parser._current_content_mode = "output"

    event = parser._event_for_text("claude --resume e36e8e23-cefd-45e0-9f73-4e43ac761a2a")

    assert event is not None
    assert event.kind == "session"
    assert parser.session_id == "e36e8e23-cefd-45e0-9f73-4e43ac761a2a"


def test_slash_command_echo_is_chrome_even_in_output_mode() -> None:
    # The PTY echoes typed slash commands (e.g. the auto-exit "/exit");
    # a lone slash-command token is TUI echo, never agent output.
    parser = ClaudeInteractiveTranscriptParser()
    parser._current_content_mode = "output"

    assert parser._event_for_text("/exit") is None
    assert parser._event_for_text("/rc") is None


def test_final_text_not_contaminated_by_exit_banner_end_to_end() -> None:
    # Regression from a live claude/haiku run: the flushed final text
    # accumulated "Resume this session with:" and slash-command echo.
    parser = ClaudeInteractiveParser()
    assistant_text = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "The sum of alpha (41) and beta (7) is 48."}]
            },
        }
    )
    lines = [
        assistant_text + "\n",
        "/exit\n",
        "Resume this session with:\n",
        "claude --resume d3f0f675-744f-4181-8f6b-2cdeeb113425\n",
    ]

    parsed = list(parser.parse(iter(lines)))

    texts = [line for line in parsed if line.type == "text"]
    assert len(texts) == 1
    assert texts[0].content == "The sum of alpha (41) and beta (7) is 48."
