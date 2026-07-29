from __future__ import annotations

from ralph.agents._bounded_text_buffer import DEFAULT_MAX_BUFFER_CHARS
from ralph.agents.parsers.claude_interactive_transcript_parser import (
    ClaudeInteractiveTranscriptParser,
)


def test_newline_free_transcript_keeps_the_newest_complete_output() -> None:
    parser = ClaudeInteractiveTranscriptParser()

    assert parser.feed("x" * (DEFAULT_MAX_BUFFER_CHARS + 1)) == []
    events = parser.feed("visible output after flood\n")

    assert [event.kind for event in events] == ["output"]
    assert events[0].text.endswith("visible output after flood")
