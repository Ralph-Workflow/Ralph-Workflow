from __future__ import annotations

import json

from ralph.agents.invoke._pty_transcript import (
    transcript_lines_from_event,
    transcript_lines_from_events,
)
from ralph.agents.parsers.claude_interactive_transcript_parser import (
    ClaudeInteractiveTranscriptParser,
)


def test_feeding_the_same_line_twice_through_one_parser_loses_the_session_event() -> None:
    raw_line = json.dumps({"type": "mode", "mode": "normal", "sessionId": "sess-double-feed"})
    parser = ClaudeInteractiveTranscriptParser()

    parser.feed(raw_line)
    second_call_lines = transcript_lines_from_event(raw_line, parser=parser)

    assert second_call_lines == [], (
        "a second feed() on the same line through the same stateful parser "
        "returns nothing -- this is the bug, pinned so it stays understood"
    )


def test_transcript_lines_from_events_reuses_the_single_feed_call() -> None:
    raw_line = json.dumps({"type": "mode", "mode": "normal", "sessionId": "sess-single-feed"})
    parser = ClaudeInteractiveTranscriptParser()

    events = parser.feed(raw_line)
    lines = transcript_lines_from_events(raw_line, events)

    assert lines == ["Session ID: sess-single-feed\n"]


def test_transcript_lines_from_events_matches_single_call_transcript_lines_from_event() -> None:
    raw_line = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": [{"type": "text", "text": "ok"}],
                    }
                ]
            },
        }
    )

    parser_a = ClaudeInteractiveTranscriptParser()
    via_wrapper = transcript_lines_from_event(raw_line, parser=parser_a)

    parser_b = ClaudeInteractiveTranscriptParser()
    events_b = parser_b.feed(raw_line)
    via_events = transcript_lines_from_events(raw_line, events_b)

    assert via_wrapper == via_events == [f"{raw_line}\n"]


def test_transcript_thread_shape_survives_a_multi_line_replay() -> None:
    lines_in = [
        json.dumps({"type": "mode", "mode": "normal", "sessionId": "sess-replay"}),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_agent_1",
                            "name": "Agent",
                            "input": {"description": "do work"},
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
                            "tool_use_id": "toolu_agent_1",
                            "content": [{"type": "text", "text": "done"}],
                        }
                    ]
                },
            }
        ),
    ]
    parser = ClaudeInteractiveTranscriptParser()
    emitted: list[str] = []
    for raw_line in lines_in:
        events = parser.feed(raw_line)
        emitted.extend(transcript_lines_from_events(raw_line, events))

    assert emitted == [
        "Session ID: sess-replay\n",
        f"{lines_in[1]}\n",
        f"{lines_in[2]}\n",
    ]
