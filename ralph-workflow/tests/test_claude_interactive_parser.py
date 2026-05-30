from __future__ import annotations

import json

import pytest

from ralph.agents.activity import AgentActivityKind
from ralph.agents.execution_state import ClaudeInteractiveExecutionStrategy
from ralph.agents.parsers.claude_interactive import (
    ClaudeInteractiveParser,
    ClaudeInteractiveTranscriptParser,
)
from ralph.display.vt_normalizer import normalize_vt_text


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
    "raw_line, expected_kind",
    [
        ("\r✢Tinkering… (5s · ↓292 tokens)\n", "thinking"),
        ("\r✶Actioning… (14s · ↓1.2k tokens)\n", "thinking"),
        ("\r✢Hullaballooing… (23s · ↓ 1.9k tokens)\n", "thinking"),
        ("\r✶Quaing\n", "thinking"),
        ("\r· thinking)\n", "thinking"),
        ("\r✢Clauding…\n", "thinking"),
        ("\r●Let me try reading the file\n", "thinking"),
        # Token counter line
        ("\r↓292 tokens\n", "thinking"),
        # Duration line
        ("\r(5s · thinking)\n", "thinking"),
        # Normal output text must still be classified as output
        ("\rNow let me explore the repository\n", "output"),
        ("\rRendered help text includes tool: read_file as an example.\n", "output"),
    ],
)
def test_interactive_parser_classifies_thinking_status_as_thinking(
    raw_line: str, expected_kind: str
) -> None:
    parser = ClaudeInteractiveTranscriptParser()

    events = parser.feed(raw_line)

    kinds = [event.kind for event in events]
    assert kinds == [expected_kind], f"Expected [{expected_kind}], got {kinds} for {raw_line!r}"


def test_interactive_parser_accumulates_thinking_status_in_thinking_accumulator() -> None:
    parser = ClaudeInteractiveParser()
    lines = iter(
        [
            "✢Tinkering… (5s · ↓292 tokens)",
            "Now let me explore the repository.",
            "claude tool: read_file",
        ]
    )

    results = list(parser.parse(lines))

    text_results = [r for r in results if r.type == "text"]
    thinking_results = [r for r in results if r.type == "thinking"]
    tool_results = [r for r in results if r.type == "tool_use"]

    assert len(text_results) == 1
    assert text_results[0].content == "Now let me explore the repository."
    assert len(thinking_results) == 1
    assert thinking_results[0].content == "✢Tinkering… (5s · ↓292 tokens)"
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

    events = parser.feed(
        "You've hit your session limit · resets 3:45pm\n"
    )

    assert [event.kind for event in events] == ["output"]
