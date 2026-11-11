from __future__ import annotations

import json

import pytest

from ralph.agents.parsers.claude_interactive import (
    ClaudeInteractiveParser,
    ClaudeInteractiveTranscriptParser,
)
from ralph.display.vt_normalizer import normalize_vt_text


def _enter_output_mode(parser: ClaudeInteractiveParser) -> None:
    envelope = json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "working on it"}]},
        }
    )
    assert [(event.kind, event.text) for event in parser.parse_transcript_entry(envelope)] == [
        ("output", "working on it")
    ]

    probe = parser.parse_transcript_entry("thinking about it\n")
    assert [(event.kind, event.text) for event in probe] == [("output", "thinking about it")]


def test_vt_normalizer_strips_charset_designation_and_cursor_save_escapes() -> None:
    raw = "/rc\x1b(B\x0f\x1b(B\x0f\x1b7\x1b8\x1b(B\x0f\x1b7\x1b8\n"

    normalized = normalize_vt_text(raw)

    assert normalized == "/rc\n"


def test_vt_normalizer_strips_shift_out_and_keypad_mode_escapes() -> None:
    raw = "\x0ehello\x0f world\x1b=\x1b>\n"

    normalized = normalize_vt_text(raw)

    assert normalized == "hello world\n"


def test_exit_banner_resume_line_is_chrome_even_in_output_mode() -> None:
    parser = ClaudeInteractiveParser()
    _enter_output_mode(parser)

    events = parser.parse_transcript_entry("Resume this session with:\n")

    assert events == []


def test_exit_banner_resume_id_line_still_yields_session() -> None:
    parser = ClaudeInteractiveParser()
    _enter_output_mode(parser)

    events = parser.parse_transcript_entry("claude --resume e36e8e23-cefd-45e0-9f73-4e43ac761a2a\n")

    assert [(event.kind, event.text) for event in events] == [
        ("session", "claude --resume e36e8e23-cefd-45e0-9f73-4e43ac761a2a")
    ]
    assert parser.session_id == "e36e8e23-cefd-45e0-9f73-4e43ac761a2a"


def test_slash_command_echo_is_chrome_even_in_output_mode() -> None:
    parser = ClaudeInteractiveParser()
    _enter_output_mode(parser)

    assert parser.parse_transcript_entry("/exit\n") == []
    assert parser.parse_transcript_entry("/rc\n") == []


def test_final_text_not_contaminated_by_exit_banner_end_to_end() -> None:
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
    parser = ClaudeInteractiveTranscriptParser()
    payload = json.dumps({"type": "assistant", "message": {"content": "plain string answer"}})

    events = parser.feed(payload + "\n")

    assert [(event.kind, event.text) for event in events] == [("output", "plain string answer")]


def test_error_event_with_string_error_payload_is_surfaced() -> None:
    parser = ClaudeInteractiveTranscriptParser()
    payload = json.dumps({"type": "error", "error": "boom: rate limited"})

    events = parser.feed(payload + "\n")

    assert [(event.kind, event.text) for event in events] == [("error", "boom: rate limited")]


@pytest.mark.parametrize(
    "error_text",
    [
        "API Error: 400 {type: error, message: invalid_request_error}",
        "Authentication error: OAuth token has expired",
        "Credential error: no API key found",
        "401 Unauthorized: invalid API key",
        "403 Forbidden: account access denied",
        "429 Too Many Requests: quota exceeded",
        "Rate limit error: retry after 60 seconds",
        "Billing error: credit balance is too low",
        "Service unavailable: Anthropic API is overloaded",
    ],
)
def test_provider_failure_text_is_surfaced_as_error_immediately(error_text: str) -> None:
    parser = ClaudeInteractiveTranscriptParser()
    assistant_payload = json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "starting work"}]},
        }
    )

    assert parser.feed(assistant_payload + "\n")
    events = parser.feed(error_text + "\n")

    assert [(event.kind, event.text) for event in events] == [("error", error_text)]
