"""Live-derived robustness tests for the interactive Claude parser.

Split from ``test_claude_interactive_parser.py`` (repo 1000-line file cap).
These pin the fixes surfaced by live-testing the interactive transport
against Claude Code 2.1.218 (haiku): VT escape residue, exit-banner chrome,
parallel same-tool call emission, is_error parity, and string-shaped JSON
payload handling.
"""

from __future__ import annotations

import json

from ralph.agents.parsers.claude_interactive import (
    ClaudeInteractiveParser,
    ClaudeInteractiveTranscriptParser,
)
from ralph.display.vt_normalizer import normalize_vt_text


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


def test_parallel_same_tool_calls_in_one_message_all_emitted() -> None:
    # Two tool_use blocks with the same tool name but distinct ids in a
    # single assistant message must both surface; the consecutive-duplicate
    # guard must key on tool_use_id, not just (kind, text).
    parser = ClaudeInteractiveTranscriptParser()
    payload = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "tu_1", "name": "Bash", "input": {"command": "ls"}},
                    {"type": "tool_use", "id": "tu_2", "name": "Bash", "input": {"command": "pwd"}},
                ]
            },
        }
    )

    events = parser.feed(payload + "\n")

    tool_uses = [event for event in events if event.kind == "tool_use"]
    assert [event.metadata.get("tool_use_id") for event in tool_uses] == ["tu_1", "tu_2"]


def test_repeated_identical_tool_use_line_still_suppressed() -> None:
    parser = ClaudeInteractiveTranscriptParser()
    payload = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "tu_1", "name": "Bash", "input": {"command": "ls"}}
                ]
            },
        }
    )

    first = parser.feed(payload + "\n")
    second = parser.feed(payload + "\n")

    assert [event.kind for event in first] == ["tool_use"]
    assert second == []


def test_identical_result_text_from_distinct_tool_calls_both_emitted() -> None:
    parser = ClaudeInteractiveTranscriptParser()

    def result_payload(tool_use_id: str) -> str:
        return json.dumps(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": [{"type": "text", "text": "48"}],
                        }
                    ]
                },
            }
        )

    first = parser.feed(result_payload("tu_1") + "\n")
    second = parser.feed(result_payload("tu_2") + "\n")

    assert [event.kind for event in first] == ["tool_result"]
    assert [event.kind for event in second] == ["tool_result"]


def test_is_error_tool_result_surfaces_as_error_with_tool_identity() -> None:
    # Parity with the headless Claude parser (and Cursor/Pi/Generic): a
    # failed tool call is an error, not a routine tool_result.
    parser = ClaudeInteractiveParser()
    tool_use = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "tu_1", "name": "Bash", "input": {"command": "x"}}
                ]
            },
        }
    )
    failed_result = json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu_1",
                        "is_error": True,
                        "content": [{"type": "text", "text": "command failed: exit 1"}],
                    }
                ]
            },
        }
    )

    parsed = list(parser.parse(iter([tool_use + "\n", failed_result + "\n"])))

    assert [line.type for line in parsed] == ["tool_use", "error"]
    assert parsed[1].content == "command failed: exit 1"
    assert parsed[1].metadata is not None
    assert parsed[1].metadata.get("tool") == "Bash"
    assert parsed[1].metadata.get("tool_use_id") == "tu_1"


def test_assistant_string_content_emitted_as_output() -> None:
    # Defensive future-proofing: an assistant message whose content is a
    # plain string (instead of a content-block list) must not be dropped.
    parser = ClaudeInteractiveTranscriptParser()
    payload = json.dumps({"type": "assistant", "message": {"content": "plain string answer"}})

    events = parser.feed(payload + "\n")

    assert [(event.kind, event.text) for event in events] == [("output", "plain string answer")]


def test_error_event_with_string_error_payload_is_surfaced() -> None:
    parser = ClaudeInteractiveTranscriptParser()
    payload = json.dumps({"type": "error", "error": "boom: rate limited"})

    events = parser.feed(payload + "\n")

    assert [(event.kind, event.text) for event in events] == [("error", "boom: rate limited")]
