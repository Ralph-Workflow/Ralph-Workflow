from __future__ import annotations

from ralph.agents.parsers.claude_interactive_transcript_parser import (
    ClaudeInteractiveTranscriptParser,
)


def test_newline_free_transcript_keeps_the_newest_complete_output() -> None:
    parser = ClaudeInteractiveTranscriptParser(max_buffer_chars=4096)

    assert parser.feed("x" * 4097) == []
    events = parser.feed("visible output after flood\n")

    assert [event.kind for event in events] == ["output"]
    assert events[0].text.endswith("visible output after flood")
